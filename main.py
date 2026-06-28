"""Token 用量监控 —— PyWebView 版本入口。

运行: python main.py
"""
import sys
import os
import threading
import platform

import webview

import config
from api import Api
from logger import log

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


def create_tray_icon():
    """创建托盘图标。"""
    try:
        from PIL import Image, ImageDraw
        size = 64
        image = Image.new('RGBA', (size, size), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)
        
        # 蓝色圆角矩形
        draw.rounded_rectangle(
            [(6, 6), (size-6, size-6)],
            radius=16,
            fill=(59, 130, 246, 255)
        )
        
        # 白色 "T" 字
        draw.text(
            (size//2, size//2),
            "T",
            fill=(255, 255, 255, 255),
            anchor="mm"
        )
        
        return image
    except Exception:
        return None


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
    cfg = config.load()
    api = Api()
    
    current_dir = os.path.dirname(os.path.abspath(__file__))
    html_path = os.path.join(current_dir, 'web', 'index.html')
    
    # 创建窗口 - 默认小面板，无边框，透明
    window = webview.create_window(
        'Token 用量监控',
        html_path,
        js_api=api,
        width=420,
        height=400,
        min_size=(260, 80),
        on_top=True,  # 屏幕级别置顶
        resizable=True,
        x=520,
        y=220,
        frameless=True,  # 无边框
        transparent=True  # 透明背景
    )
    
    # 保存窗口引用到 API
    api.window = window
    
    # 窗口关闭回调
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
    
    # 启动 WebView（macOS 上设置窗口级别和透明度）
    def on_loaded():
        # 窗口加载完成后设置置顶
        if platform.system() == 'Darwin':
            set_window_on_top_macos(True)
            # 设置初始透明度
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
