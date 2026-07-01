"""Token 用量监控 —— PyWebView 版本入口。

运行: python -X utf8 main.py
"""
import os
import platform

import webview

import config
from api import Api
from logger import log


def enable_windows_dpi_awareness():
    """Windows 高 DPI：优先启用 Per-Monitor DPI Aware v2。"""
    if platform.system() != 'Windows':
        return
    try:
        import ctypes
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
            log("Windows DPI 感知已设置: Per-Monitor")
            return
        except Exception:
            pass
        ctypes.windll.user32.SetProcessDPIAware()
        log("Windows DPI 感知已设置: System")
    except Exception as e:
        log(f"设置 Windows DPI 感知失败: {e}")


# macOS 特定：设置窗口级别
if platform.system() == 'Darwin':
    try:
        import objc
        from Cocoa import NSApplication, NSWindow
        from Quartz import kCGFloatingWindowLevel, kCGNormalWindowLevel
        HAS_MACOS_API = True
    except ImportError:
        HAS_MACOS_API = False
        log("macOS Cocoa API 不可用")
else:
    HAS_MACOS_API = False


def set_window_on_top_macos(on_top=True):
    """macOS 特定：设置窗口级别为浮动（置顶）。"""
    if not HAS_MACOS_API:
        return

    try:
        app = NSApplication.sharedApplication()
        windows = app.windows()
        if windows:
            window = windows[0]
            if on_top:
                window.setLevel_(kCGFloatingWindowLevel)
            else:
                window.setLevel_(kCGNormalWindowLevel)
            log(f"macOS 窗口级别已设置: {'置顶' if on_top else '正常'}")
    except Exception as e:
        log(f"设置 macOS 窗口级别失败: {e}")


def main():
    enable_windows_dpi_awareness()

    cfg = config.load()
    api = Api()

    current_dir = os.path.dirname(os.path.abspath(__file__))
    html_path = os.path.join(current_dir, 'web', 'index.html')

    is_windows = platform.system() == 'Windows'

    window = webview.create_window(
        'Token 用量监控',
        html_path,
        js_api=api,
        width=420,
        height=400,
        min_size=(260, 80),
        on_top=True,
        resizable=True,
        x=100,
        y=100,
        frameless=True,
        transparent=not is_windows
    )

    api.window = window

    # Windows: pywebview get_functions 会递归遍历 api.window.native
    #（.NET WinForms Form），导致 COM 无限递归崩溃。标记不可遍历以避开。
    if is_windows:
        window._serializable = False

    def on_closed():
        try:
            compact = window.evaluate_js('state.compact') or False
            dock = window.evaluate_js('state.dock') or False
            cfg['compact'] = compact
            cfg['dock'] = dock
            config.save(cfg)
        except Exception as e:
            log(f"保存配置失败: {e}")

    window.events.closed += on_closed

    def on_loaded():
        if platform.system() == 'Darwin':
            set_window_on_top_macos(True)
            opacity = cfg.get('opacity', 0.92)
            try:
                from Cocoa import NSApplication
                app = NSApplication.sharedApplication()
                windows = app.windows()
                if windows:
                    window_obj = windows[0]
                    window_obj.setAlphaValue_(opacity)
                    log(f"初始透明度已设置: {opacity}")
            except Exception as e:
                log(f"设置初始透明度失败: {e}")

    window.events.loaded += on_loaded

    webview.start(debug=False)


if __name__ == "__main__":
    main()
