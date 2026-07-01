"""窗口几何操作（移动 / 缩放 / 置顶 / 透明度 / 几何持久化）。

跨平台注意事项：
  - Windows: 用 Win32 SetWindowPos 处理 DPI 缩放
  - macOS:   用 Cocoa NSWindow frame 操作（必须主线程）
  - Linux:   pywebview resize/move 兜底
"""
from __future__ import annotations

import ctypes
import platform
from ctypes import wintypes
from typing import Any

import config
from logger import log

from . import screen as screen_helper


# --------------------- 置顶 ---------------------

def set_always_on_top(window, on_top: bool) -> bool:
    """切换屏幕级别置顶。macOS 走 Cocoa 窗口级别，其他走 pywebview。"""
    if platform.system() == "Darwin":
        try:
            from Cocoa import NSApplication
            from Quartz import (kCGFloatingWindowLevel,
                               kCGNormalWindowLevel)
            app = NSApplication.sharedApplication()
            windows = app.windows()
            if windows:
                w = windows[0]
                w.setLevel_(kCGFloatingWindowLevel if on_top
                            else kCGNormalWindowLevel)
        except ImportError as e:
            log(f"macOS 窗口级别设置失败: {e}")

    if window is not None:
        window.on_top = on_top
    return True


# --------------------- 透明度 ---------------------

def set_opacity(window, opacity: float) -> bool:
    """设置窗口透明度（0.3~1.0）。macOS 走 Cocoa alpha。"""
    opacity = max(0.3, min(1.0, opacity))
    if platform.system() == "Darwin" and window is not None:
        try:
            from Cocoa import NSApplication
            app = NSApplication.sharedApplication()
            windows = app.windows()
            if windows:
                w = windows[0]
                w.setAlphaValue_(opacity)
                log(f"macOS 窗口透明度已设置: {opacity}")
        except ImportError as e:
            log(f"macOS 透明度设置失败: {e}")
    return True


# --------------------- 几何持久化 ---------------------

def save_geometry(cfg: dict, x: int, y: int, w: int, h: int) -> None:
    cfg["geometry"] = [x, y, w, h]
    config.save(cfg)


def get_geometry(cfg: dict):
    return cfg.get("geometry")


# --------------------- 按内容收缩窗口 ---------------------

def resize_to_content(window, top_mode_width: int, css_width: int,
                      css_height: int) -> dict:
    """按前端报告的内容尺寸收缩窗口。返回 {"ok", "width", "height"}。"""
    if window is None:
        return {"ok": False}

    css_width = int(css_width or 0)
    keep_width = css_width <= 0
    css_height = max(80, min(1200, int(css_height)))

    layout = screen_helper.screen_layout(window)
    scale = float(layout.get("scale", 1.0) or 1.0)

    # layout 是物理像素，JS 传来 CSS 逻辑像素，统一用 CSS 比较
    phys_w = max(260, int(layout.get("width", 1200)))
    phys_h = max(80, int(layout.get("height", 800)))
    screen_w_css = int(phys_w / scale) if scale > 0 else phys_w
    screen_h_css = int(phys_h / scale) if scale > 0 else phys_h
    max_w = screen_w_css if top_mode_width else max(260, int(screen_w_css * 0.92))
    max_h = screen_h_css

    if top_mode_width:
        width = int(top_mode_width)
    elif keep_width:
        width = int(top_mode_width or screen_helper.current_window_width(window))
    else:
        width = max(260, css_width)
    width = max(260, min(width, max_w))
    height = max(80, min(css_height, max_h))

    system = platform.system()
    if system == "Darwin" and _resize_macos(window, width, height, keep_width):
        return {"ok": True, "width": width, "height": height}
    if system == "Windows" and _resize_windows(window, width, height):
        return {"ok": True, "width": width, "height": height}

    # 兜底
    x = int(getattr(window, "x", 0) or 0)
    y = int(getattr(window, "y", 0) or 0)
    window.resize(width, height)
    cfg_path = getattr(window, "_token_view_cfg", None)
    if cfg_path is not None:
        cfg_path["geometry"] = [x, y, width, height]
        config.save(cfg_path)
    log(f"窗口已按内容缩放: w={width}, h={height}")
    return {"ok": True, "width": width, "height": height}


def _resize_macos(window, width: int, height: int, keep_width: bool) -> bool:
    try:
        def work():
            native = getattr(window, "native", None)
            target_w = width
            if keep_width:
                target_w = screen_helper.current_window_width(window)
            if native is not None:
                frame = native.frame()
                frame.size.width = target_w
                frame.size.height = height
                native.setFrame_display_(frame, True)
            else:
                window.resize(target_w, height)
            return target_w
        screen_helper.run_on_macos_main_thread(work)
        return True
    except (OSError, TimeoutError) as e:
        log(f"macOS 按内容缩放失败: {e}")
        return False


def _resize_windows(window, width: int, height: int) -> bool:
    """Win32 SetWindowPos 走物理像素，JS 传 CSS 逻辑像素。"""
    try:
        user32 = ctypes.windll.user32
    except OSError:
        return False
    hwnd = screen_helper.window_hwnd(window)
    if not hwnd:
        return False

    scale = screen_helper.windows_dpi_scale(window)
    phys_w = max(1, int(width * scale))
    phys_h = max(1, int(height * scale))

    HWND_TOPMOST = -1
    SWP_NOACTIVATE = 0x0010

    class RECT(ctypes.Structure):
        _fields_ = [
            ("left", wintypes.LONG),
            ("top", wintypes.LONG),
            ("right", wintypes.LONG),
            ("bottom", wintypes.LONG),
        ]

    rect = RECT()
    if not user32.GetWindowRect(wintypes.HWND(hwnd), ctypes.byref(rect)):
        return False

    ok = user32.SetWindowPos(
        wintypes.HWND(hwnd),
        wintypes.HWND(HWND_TOPMOST),
        int(rect.left),
        int(rect.top),
        phys_w,
        phys_h,
        SWP_NOACTIVATE,
    )
    if not ok:
        return False
    cfg = getattr(window, "_token_view_cfg", None)
    if cfg is not None:
        cfg["geometry"] = [int(rect.left), int(rect.top), width, height]
        config.save(cfg)
    log(f"Windows 窗口已按内容缩放: w={width}, h={height} "
        f"(phys={phys_w}x{phys_h}, scale={scale:.2f})")
    return True


# --------------------- 移到屏幕顶部 ---------------------

def move_to_top(window, cfg: dict, height: int = 0) -> dict:
    """把窗口放大并移到当前屏幕顶部。返回 {"ok", "width", "height", "mode"}。"""
    if window is None:
        return {"ok": False}

    system = platform.system()
    if system == "Darwin":
        return _move_to_top_macos(window, cfg, height)
    if system == "Windows":
        r = _move_to_top_windows(window, cfg, height)
        if r.get("ok"):
            return r

    # 兜底（Linux 等）
    return _move_to_top_pywebview(window, cfg, height)


def _move_to_top_macos(window, cfg: dict, height: int) -> dict:
    try:
        def work():
            native = getattr(window, "native", None)
            if native is None:
                return {"ok": False, "error": "未找到 macOS 原生窗口"}

            screen = native.screen()
            if screen is None:
                from Cocoa import NSScreen
                screen = NSScreen.mainScreen()
            if screen is None:
                return {"ok": False, "error": "未找到 macOS 屏幕"}

            visible = screen.visibleFrame()
            sw = max(320, int(visible.size.width))
            sh = max(120, int(visible.size.height))
            top_margin = 36
            target_w = max(320, int(sw * 0.8))
            frame = native.frame()
            current_h = int(frame.size.height or 0)
            target_h = max(80, min(int(height or current_h),
                                   max(80, sh - top_margin)))
            target_x = int(visible.origin.x + (sw - target_w) / 2)
            target_y = int(visible.origin.y + sh - target_h - top_margin)

            frame.origin.x = target_x
            frame.origin.y = target_y
            frame.size.width = target_w
            frame.size.height = target_h
            native.setFrame_display_(frame, True)
            native.orderFrontRegardless()

            cfg["geometry"] = [target_x,
                               int(visible.origin.y + top_margin),
                               target_w, target_h]
            config.save(cfg)
            return {"ok": True, "width": target_w, "height": target_h, "mode": "top"}
        return screen_helper.run_on_macos_main_thread(work)
    except (OSError, TimeoutError) as e:
        log(f"macOS 移动到顶部失败: {e}")
        return {"ok": False, "error": str(e)}


def _move_to_top_windows(window, cfg: dict, height: int) -> dict:
    try:
        user32 = ctypes.windll.user32
    except OSError:
        return {"ok": False}
    hwnd = screen_helper.window_hwnd(window)
    if not hwnd:
        return {"ok": False}

    MONITOR_DEFAULTTONEAREST = 2
    SWP_NOACTIVATE = 0x0010
    HWND_TOPMOST = -1

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
        return {"ok": False}

    info = MONITORINFO()
    info.cbSize = ctypes.sizeof(MONITORINFO)
    if not user32.GetMonitorInfoW(monitor, ctypes.byref(info)):
        return {"ok": False}

    scale = screen_helper.windows_dpi_scale(window)
    work = info.rcWork
    sw = max(320, int(work.right - work.left))
    sh = max(120, int(work.bottom - work.top))
    target_w = max(320, int(sw * 0.8))
    target_h = max(80, min(int((height or 0) * scale), sh))
    target_x = int(work.left + (sw - target_w) / 2)
    target_y = int(work.top)

    ok = user32.SetWindowPos(
        wintypes.HWND(hwnd),
        wintypes.HWND(HWND_TOPMOST),
        target_x, target_y, target_w, target_h, SWP_NOACTIVATE,
    )
    if not ok:
        return {"ok": False}

    # 返回 CSS 逻辑像素给 JS
    css_w = max(260, int(target_w / scale)) if scale > 0 else target_w
    css_h = max(80, int(target_h / scale)) if scale > 0 else target_h
    cfg["geometry"] = [target_x, target_y, target_w, target_h]
    config.save(cfg)
    log(f"Windows 窗口已移动到顶部: x={target_x}, y={target_y}, "
        f"w={target_w}, h={target_h} (css={css_w}x{css_h}, scale={scale:.2f})")
    return {"ok": True, "width": css_w, "height": css_h, "mode": "top"}


def _move_to_top_pywebview(window, cfg: dict, height: int) -> dict:
    try:
        import webview
    except ImportError:
        return {"ok": False, "error": "pywebview 不可用"}
    x = int(getattr(window, "x", 0) or 0)
    y = int(getattr(window, "y", 0) or 0)
    w = int(getattr(window, "width", 0) or 0)
    h = max(80, int(height or getattr(window, "height", 0) or 0))
    screens = screen_helper.get_webview_screens(webview)
    screen = None
    if screens and w and h:
        cx = x + w // 2
        cy = y + h // 2
        for item in screens:
            sx = screen_helper.screen_value(item, "x")
            sy = screen_helper.screen_value(item, "y")
            sw = screen_helper.screen_value(item, "width")
            sh = screen_helper.screen_value(item, "height")
            if sx <= cx < sx + sw and sy <= cy < sy + sh:
                screen = item
                break
        if screen is None:
            screen = screens[0]
    if screen is not None:
        x, y, w, h = screen_helper.top_bar_geometry(screen, h)
    else:
        w = max(1200, int(w))
        x = 40
        y = 36
    window.resize(w, h)
    window.move(x, y)
    if w and h:
        cfg["geometry"] = [x, y, w, h]
        config.save(cfg)
    log(f"窗口已放大并移动到顶部: x={x}, y={y}, w={w}, h={h}")
    return {"ok": True, "width": w, "height": h, "mode": "top"}
