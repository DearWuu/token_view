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
    """切换屏幕级别置顶。

    Windows 必须用 Win32 SetWindowPos(HWND_TOPMOST/HWND_NOTOPMOST)：
    .NET Form.TopMost 设回 false 时偶发不生效（WinForms 已知行为，
    切回需要额外 BringToFront）。pywebview.window.on_top = False
    走的就是 Form.TopMost，所以这里绕开，直接走原生 API。
    macOS 走 Cocoa 窗口级别。
    """
    if platform.system() == "Windows" and window is not None:
        hwnd = screen_helper.window_hwnd(window)
        if hwnd:
            try:
                ctypes.windll.user32.SetWindowPos(
                    wintypes.HWND(hwnd),
                    wintypes.HWND(-1 if on_top else -2),   # HWND_TOPMOST / HWND_NOTOPMOST
                    0, 0, 0, 0,
                    0x0003,                                   # SWP_NOMOVE | SWP_NOSIZE
                )
                log(f"Windows 屏幕置顶已{'开启' if on_top else '关闭'}")
            except OSError as e:
                log(f"Windows 置顶切换失败: {e}")
        return True

    if platform.system() == "Darwin":
        try:
            from Cocoa import NSApplication
            from Quartz import (kCGFloatingWindowLevel,
                               kCGNormalWindowLevel)
            app = NSApplication.sharedApplication()
            ns_windows = app.windows()
            if ns_windows:
                ns_windows[0].setLevel_(kCGFloatingWindowLevel if on_top
                                         else kCGNormalWindowLevel)
        except ImportError as e:
            log(f"macOS 窗口级别设置失败: {e}")
        return True

    return False


# --------------------- 透明度 ---------------------

def set_opacity(window, opacity: float) -> bool:
    """运行时改窗口透明度。

    设计选择：用 CSS 假透明度（背景 rgba），不调原生窗口 alpha。
    原因：WS_EX_LAYERED + LWA_ALPHA 是整窗 alpha 混合，文字/进度条也
    会一起淡化，但视觉上大块背景穿透感强、细线条不显透明，用户
    反馈"只淡化背景"。CSS rgba 只动背景色，文字/进度条保持清晰。
    实际透明度变化交给前端 CSS 变量（applyWindowOpacity）。

    本函数只负责：把透明度持久化到 cfg，并（若 main 窗口存在）调一次
    evaluate_js 通知前端应用。窗口层不做任何操作。
    """
    return True  # 实际效果在 api.core.Api.set_opacity 里通过 evaluate_js 触发


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


# --------------------- 移动窗口（不改大小） ---------------------

def move_window(window, x: int, y: int) -> bool:
    """只改窗口位置，不改大小。CSS 逻辑像素。"""
    if window is None:
        return False

    system = platform.system()
    if system == "Windows":
        hwnd = screen_helper.window_hwnd(window)
        if hwnd:
            try:
                scale = screen_helper.windows_dpi_scale(window)
                user32 = ctypes.windll.user32
                phys_x = int(x * scale)
                phys_y = int(y * scale)
                user32.SetWindowPos(
                    wintypes.HWND(hwnd), None,
                    phys_x, phys_y, 0, 0,
                    0x0001 | 0x0004 | 0x0010,    # SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE
                )
                return True
            except OSError as e:
                log(f"Windows move_window 失败: {e}")
                return False

    try:
        window.move(x, y)
        return True
    except (OSError, RuntimeError, AttributeError) as e:
        log(f"move_window 失败: {e}")
        return False


def user_resize(window, x: int = -1, y: int = -1, width: int = -1, height: int = -1) -> dict:
    """用户拖 8 方向 resize handle 时由前端调用，参数是 CSS 逻辑像素。

    frameless 窗口没有 OS resize 边，前端 HTML 自绘 8 方向 handle 调这个。
    x/y 为 -1 表示该方向不动（保持当前位置）—— 拖右/下/右上/右下 时只改 w/h，
    拖左/上/左上/左下 时同时改 x/y + w/h。
    Windows 必须走 Win32 SetWindowPos 处理 DPI；其他平台 window.resize 够用。
    """
    if window is None:
        return {"ok": False}

    if width < 0 or height < 0:
        return {"ok": False, "error": "width/height required"}
    # 限制最小尺寸
    width = max(260, int(width))
    height = max(80, min(1200, int(height)))
    log(f"user_resize: x={x}, y={y}, w={width}, h={height}")

    system = platform.system()
    if system == "Windows" and _resize_windows(window, width, height, x, y):
        return {"ok": True, "width": width, "height": height}

    try:
        window.resize(width, height)
        cfg = getattr(window, "_token_view_cfg", None)
        if cfg is not None:
            cx = int(getattr(window, "x", 0) or 0)
            cy = int(getattr(window, "y", 0) or 0)
            cfg["geometry"] = [cx, cy, width, height]
            config.save(cfg)
        return {"ok": True, "width": width, "height": height}
    except (OSError, RuntimeError) as e:
        log(f"user_resize 失败: {e}")
        return {"ok": False, "error": str(e)}


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


def _resize_windows(window, width: int, height: int,
                    x: int = -1, y: int = -1) -> bool:
    """Win32 SetWindowPos 走物理像素，JS 传 CSS 逻辑像素。

    x/y = -1 表示保持当前位置。>=0 时物理像素换算后应用。
    """
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

    if x >= 0 and y >= 0:
        phys_x = int(x * scale)
        phys_y = int(y * scale)
    else:
        # 保持当前位置：从 GetWindowRect 读
        rect = wintypes.RECT()
        if user32.GetWindowRect(wintypes.HWND(hwnd), ctypes.byref(rect)):
            phys_x = int(rect.left)
            phys_y = int(rect.top)
        else:
            phys_x, phys_y = 0, 0

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
        phys_x,
        phys_y,
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

def _save_pre_dock_geometry(window, cfg: dict) -> None:
    """进入 dock 前把当前窗口位置存到 cfg["pre_dock_geometry"]，退出时恢复。"""
    if not cfg.get("pre_dock_geometry"):
        try:
            x = int(getattr(window, "x", 0) or 0)
            y = int(getattr(window, "y", 0) or 0)
            w = int(getattr(window, "width", 0) or 0)
            h = int(getattr(window, "height", 0) or 0)
            if w and h:
                cfg["pre_dock_geometry"] = [x, y, w, h]
                config.save(cfg)
        except (OSError, AttributeError):
            pass


def move_to_top(window, cfg: dict, height: int = 0) -> dict:
    """把窗口放大并移到当前屏幕顶部。返回 {"ok", "width", "height", "mode"}。"""
    if window is None:
        return {"ok": False}

    _save_pre_dock_geometry(window, cfg)

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


def restore_from_dock(window, cfg: dict) -> dict:
    """退出 auto-hide dock 时把窗口恢复到 pre_dock_geometry。"""
    if window is None:
        return {"ok": False, "error": "no window"}
    pre = cfg.get("pre_dock_geometry")
    if not pre or len(pre) != 4:
        return {"ok": False, "error": "no pre_dock_geometry"}
    x, y, w, h = [int(v) for v in pre]
    if w < 260 or h < 80:
        return {"ok": False, "error": "invalid pre geometry"}

    system = platform.system()
    if system == "Windows" and screen_helper.window_hwnd(window):
        try:
            ctypes.windll.user32.SetWindowPos(
                wintypes.HWND(screen_helper.window_hwnd(window)),
                wintypes.HWND(-1),  # HWND_TOPMOST（保持置顶）
                x, y, w, h,
                0x0010,             # SWP_NOACTIVATE
            )
        except OSError as e:
            log(f"Windows restore_window SetWindowPos 失败: {e}")
            return {"ok": False, "error": str(e)}
    else:
        try:
            window.move(x, y)
            window.resize(w, h)
        except (OSError, RuntimeError) as e:
            log(f"restore_window 失败: {e}")
            return {"ok": False, "error": str(e)}

    cfg["geometry"] = [x, y, w, h]
    cfg.pop("pre_dock_geometry", None)
    config.save(cfg)
    log(f"窗口已从 dock 恢复: x={x}, y={y}, w={w}, h={h}")
    return {"ok": True, "x": x, "y": y, "w": w, "h": h}


# --------------------- auto-hide dock：物理位置滑入/滑出 ---------------------

def set_dock_hidden(window, hidden: bool) -> dict:
    """auto-hide dock 的物理位置切换：
    hidden=True  → 窗口 y 移到 -height+4（露 4px 缝在屏幕顶部）
    hidden=False → 窗口 y 回到 0（完整显示）

    物理位置方案比 CSS transform 稳：WebView2 高 DPI 下 calc(-100% + 4px)
    行为不一致，物理 SetWindowPos 精准控制。
    4px 缝仍在 WebView 视口内（窗口仍占 y=-h+4 ~ 4），鼠标进 4px 缝
    时 mouseenter/mousemove 仍能触发。
    """
    if window is None:
        return {"ok": False, "error": "no window"}
    hwnd = screen_helper.window_hwnd(window) if platform.system() == "Windows" else 0

    if hwnd:
        try:
            # 取当前窗口位置
            rect = wintypes.RECT()
            if not ctypes.windll.user32.GetWindowRect(wintypes.HWND(hwnd), ctypes.byref(rect)):
                return {"ok": False, "error": "GetWindowRect failed"}
            cur_x = int(rect.left)
            cur_y = int(rect.top)
            w = int(rect.right - rect.left)
            h = int(rect.bottom - rect.top)
            new_y = (4 - h) if hidden else 0
            ctypes.windll.user32.SetWindowPos(
                wintypes.HWND(hwnd),
                wintypes.HWND(-1),  # HWND_TOPMOST（保持置顶）
                cur_x, new_y, w, h,
                0x0010,             # SWP_NOACTIVATE
            )
            log(f"auto-hide dock: y={cur_y} -> {new_y} (hidden={hidden})")
            return {"ok": True, "y": new_y, "hidden": hidden}
        except OSError as e:
            log(f"set_dock_hidden Win32 失败: {e}")
            return {"ok": False, "error": str(e)}
    else:
        # macOS / Linux 兜底
        try:
            h = int(getattr(window, "height", 0) or 0)
            x = int(getattr(window, "x", 0) or 0)
            new_y = (4 - h) if hidden else 0
            window.move(x, new_y)
            return {"ok": True, "y": new_y, "hidden": hidden}
        except (OSError, RuntimeError, AttributeError) as e:
            log(f"set_dock_hidden 失败: {e}")
            return {"ok": False, "error": str(e)}
