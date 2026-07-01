"""跨平台屏幕工作区 / 窗口几何 helper。

把 main.py 早先散落在 api.py 里的 Windows Win32、macOS Cocoa、pywebview
兜底逻辑集中到这里。WebView2 / WKWebView 的尺寸在不同 DPI / Retina 下
行为差异很大，必须用原生 API 才能算准。
"""
from __future__ import annotations

import ctypes
import platform
import threading
import time
from ctypes import wintypes
from typing import Any, Optional

from logger import log

# macOS 主线程同步执行：避免 Cocoa API 在非主线程偶发不生效
_MAC_RUN_TIMEOUT = 3.0


def run_on_macos_main_thread(func, timeout: float = _MAC_RUN_TIMEOUT):
    """在 macOS 主线程同步执行 func，跨平台不可用时退化为直接调用。"""
    system = platform.system()
    if system != "Darwin":
        return func()

    try:
        from Foundation import NSThread
        from PyObjCTools import AppHelper
    except ImportError:
        log("macOS 缺少 Foundation/PyObjCTools，直接调用")
        return func()

    if NSThread.isMainThread():
        return func()

    done = threading.Event()
    box: dict = {}

    def _wrapper():
        try:
            box["value"] = func()
        except Exception as e:  # noqa: BLE001
            box["error"] = e
        finally:
            done.set()

    AppHelper.callAfter(_wrapper)
    if not done.wait(timeout):
        raise TimeoutError("等待 macOS 主线程执行窗口操作超时")
    if "error" in box:
        raise box["error"]
    return box.get("value")


# --------------------- pywebview 屏幕列表兼容 ---------------------

def get_webview_screens(webview_module) -> list:
    """兼容不同 pywebview 版本的 screens 字段（有的属性，有的可调用）。"""
    screens = getattr(webview_module, "screens", [])
    if callable(screens):
        return screens()
    return screens or []


def screen_value(screen, key: str, default: int = 0) -> int:
    """兼容 pywebview Screen 对象和 dict 两种形态。"""
    if isinstance(screen, dict):
        v = screen.get(key, default)
    else:
        v = getattr(screen, key, default)
    return int(v or default)


# --------------------- 屏幕工作区（哪个屏幕、可用区域） ---------------------

def screen_layout(window) -> dict:
    """返回当前窗口所在屏幕的工作区，单位是物理像素。

    格式：{"x", "y", "width", "height", "scale"}，出错兜底 1200x800。
    """
    system = platform.system()
    if system == "Darwin":
        layout = run_on_macos_main_thread(
            lambda: _screen_layout_macos(window))
        if layout:
            return layout
    elif system == "Windows":
        layout = _screen_layout_windows(window)
        if layout:
            return layout
    return _screen_layout_pywebview(window)


def _screen_layout_macos(window) -> Optional[dict]:
    native = getattr(window, "native", None)
    screen = native.screen() if native is not None else None
    if screen is None:
        from Cocoa import NSScreen
        screen = NSScreen.mainScreen()
    if screen is None:
        return None
    visible = screen.visibleFrame()
    return {
        "x": int(visible.origin.x),
        "y": int(visible.origin.y),
        "width": int(visible.size.width),
        "height": int(visible.size.height),
        "scale": float(screen.backingScaleFactor() or 1.0),
    }


def _screen_layout_windows(window) -> Optional[dict]:
    try:
        user32 = ctypes.windll.user32
    except OSError:
        return None

    hwnd = window_hwnd(window)
    if not hwnd:
        return None

    MONITOR_DEFAULTTONEAREST = 2

    class RECT(ctypes.Structure):
        _fields_ = [
            ("left", wintypes.LONG),
            ("top", wintypes.LONG),
            ("right", wintypes.LONG),
            ("bottom", wintypes.LONG),
        ]

    class MONITORINFO(ctypes.Structure):
        _fields_ = [
            ("cbSize", wintypes.DWORD),
            ("rcMonitor", RECT),
            ("rcWork", RECT),
            ("dwFlags", wintypes.DWORD),
        ]

    monitor = user32.MonitorFromWindow(wintypes.HWND(hwnd), MONITOR_DEFAULTTONEAREST)
    if not monitor:
        return None
    info = MONITORINFO()
    info.cbSize = ctypes.sizeof(MONITORINFO)
    if not user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
        return None

    work = info.rcWork
    scale = 1.0
    try:
        get_dpi = getattr(user32, "GetDpiForWindow", None)
        if get_dpi:
            scale = float(get_dpi(wintypes.HWND(hwnd)) or 96) / 96.0
    except OSError:
        pass
    return {
        "x": int(work.left),
        "y": int(work.top),
        "width": int(work.right - work.left),
        "height": int(work.bottom - work.top),
        "scale": scale,
    }


def _screen_layout_pywebview(window) -> dict:
    try:
        import webview
    except ImportError:
        return {"x": 0, "y": 0, "width": 1200, "height": 800, "scale": 1.0}
    screens = get_webview_screens(webview)
    x = int(getattr(window, "x", 0) or 0)
    y = int(getattr(window, "y", 0) or 0)
    w = int(getattr(window, "width", 0) or 0)
    h = int(getattr(window, "height", 0) or 0)
    cx = x + max(1, w) // 2
    cy = y + max(1, h) // 2
    screen = None
    for item in screens:
        sx = screen_value(item, "x")
        sy = screen_value(item, "y")
        sw = screen_value(item, "width")
        sh = screen_value(item, "height")
        if sx <= cx < sx + sw and sy <= cy < sy + sh:
            screen = item
            break
    if screen is None and screens:
        screen = screens[0]
    if screen is not None:
        return {
            "x": screen_value(screen, "x"),
            "y": screen_value(screen, "y"),
            "width": screen_value(screen, "width", 1200),
            "height": screen_value(screen, "height", 800),
            "scale": 1.0,
        }
    return {"x": 0, "y": 0, "width": 1200, "height": 800, "scale": 1.0}


# --------------------- Windows HWND ---------------------

def window_hwnd(window) -> int:
    """读取 Windows 原生窗口句柄。pywebview 走 .NET WinForms，handle 可能在多处。"""
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


# --------------------- 顶部条几何 ---------------------

def top_bar_geometry(screen, height: int) -> tuple:
    """计算顶部条位置：当前屏幕居中，长度约为屏幕的 80%。"""
    sx = screen_value(screen, "x")
    sy = screen_value(screen, "y")
    sw = max(320, screen_value(screen, "width", 1200))
    sh = max(120, screen_value(screen, "height", 800))
    top_margin = 36
    target_w = max(320, int(sw * 0.8))
    target_h = max(80, min(int(height or 0), max(80, sh - top_margin)))
    target_x = sx + max(0, (sw - target_w) // 2)
    target_y = sy + top_margin
    return target_x, target_y, target_w, target_h


def current_window_width(window, default: int = 260) -> int:
    """读取窗口真实宽度，macOS 原生 frame 优先。"""
    try:
        native = getattr(window, "native", None)
        if native is not None:
            frame = native.frame()
            width = int(frame.size.width or 0)
            if width > 0:
                return max(default, width)
    except (OSError, AttributeError):
        pass
    return max(default, int(getattr(window, "width", 0) or 0))


# --------------------- Windows 高 DPI 缩放 ---------------------

def windows_dpi_scale(window) -> float:
    """读取当前窗口的 DPI 缩放比（1.0=100%）。非 Windows 永远返回 1.0。"""
    if platform.system() != "Windows":
        return 1.0
    try:
        user32 = ctypes.windll.user32
    except OSError:
        return 1.0
    hwnd = window_hwnd(window)
    if not hwnd:
        return 1.0
    try:
        get_dpi = getattr(user32, "GetDpiForWindow", None)
        if get_dpi:
            dpi = get_dpi(wintypes.HWND(hwnd))
            if dpi > 0:
                return dpi / 96.0
    except OSError:
        pass
    return 1.0
