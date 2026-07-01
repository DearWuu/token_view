"""Python API 层 - 供前端 JavaScript 调用。

提供完整的配置管理、Provider 管理、CDP 启动等功能。
"""

import os
import subprocess
import threading
import time
import json
from typing import List, Dict, Any, Optional

import providers
import config
from logger import log

# Chrome 查找
def find_chrome() -> str:
    """查找 Chrome 可执行文件路径。"""
    import platform
    system = platform.system()
    
    if system == "Darwin":
        candidates = [
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            os.path.expanduser("~/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
        ]
        for p in candidates:
            if os.path.exists(p):
                return p
    elif system == "Windows":
        candidates = [
            os.environ.get("PROGRAMFILES", r"C:\Program Files"),
            os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)"),
            os.environ.get("LOCALAPPDATA", ""),
        ]
        for base in candidates:
            if not base:
                continue
            p = os.path.join(base, "Google", "Chrome", "Application", "chrome.exe")
            if os.path.exists(p):
                return p
    else:
        for name in ["google-chrome", "google-chrome-stable", "chromium-browser", "chromium"]:
            result = subprocess.run(["which", name], capture_output=True, text=True)
            if result.stdout.strip():
                return result.stdout.strip()
    return ""


class Api:
    """PyWebView JavaScript API 接口。"""

    def __init__(self):
        self.cfg = config.load()
        self._lock = threading.Lock()
        self.window = None  # 将在 main.py 中设置

    # ---- 数据相关 ----

    def get_usage(self) -> List[Dict[str, Any]]:
        """获取所有 provider 的用量数据。"""
        results = []
        providers_cfg = [p for p in self.cfg.get("providers", []) if p.get("enabled")]

        for p in providers_cfg:
            try:
                provider = providers.build(p)
                data = provider.fetch()
                result = {
                    "id": p["id"],
                    "name": p.get("name") or p.get("type"),
                    "type": p.get("type", ""),
                    "level": data.plan_level,
                    "status": data.status,
                    "error": data.error,
                    "items": [
                        {
                            "label": item.label,
                            "percent": item.used_percent,
                            "reset_at": item.reset_at,
                            "note": item.note
                        }
                        for item in data.items
                    ]
                }
                results.append(result)
            except Exception as e:
                log(f"Provider {p.get('type')} 错误: {e}")
                results.append({
                    "id": p["id"],
                    "name": p.get("name") or p.get("type"),
                    "type": p.get("type", ""),
                    "status": "error",
                    "error": str(e),
                    "items": []
                })

        return results

    def get_config(self) -> Dict[str, Any]:
        """获取当前配置。"""
        return {
            "providers": self.cfg.get("providers", []),
            "refresh_interval": self.cfg.get("refresh_interval", 60),
            "opacity": self.cfg.get("opacity", 0.92),
            "compact": self.cfg.get("compact", False),
            "dock": self.cfg.get("dock", False),
            "always_on_top": self.cfg.get("always_on_top", True),
            "geometry": self.cfg.get("geometry")
        }

    def save_config(self, updates: Dict[str, Any]) -> bool:
        """保存配置更新并应用。"""
        try:
            with self._lock:
                self.cfg.update(updates)
                config.save(self.cfg)
            
            # 应用透明度
            if 'opacity' in updates:
                self.set_opacity(updates['opacity'])
            
            log("配置已保存并应用")
            return True
        except Exception as e:
            log(f"保存配置失败: {e}")
            return False

    # ---- Provider 管理 ----

    def add_provider(self, ptype: str) -> Dict[str, Any]:
        """添加新的 provider。"""
        new_p = config.new_provider(ptype)
        with self._lock:
            providers_list = self.cfg.get("providers", [])
            providers_list.append(new_p)
            self.cfg["providers"] = providers_list
            config.save(self.cfg)
        log(f"已添加 {ptype} provider")
        return new_p

    def remove_provider(self, provider_id: str) -> bool:
        """删除 provider。"""
        try:
            with self._lock:
                providers_list = self.cfg.get("providers", [])
                self.cfg["providers"] = [p for p in providers_list if p["id"] != provider_id]
                config.save(self.cfg)
            log(f"已删除 provider {provider_id}")
            return True
        except Exception as e:
            log(f"删除 provider 失败: {e}")
            return False

    def update_provider(self, provider_id: str, updates: Dict[str, Any]) -> bool:
        """更新 provider 配置。"""
        try:
            with self._lock:
                providers_list = self.cfg.get("providers", [])
                for p in providers_list:
                    if p["id"] == provider_id:
                        p.update(updates)
                        break
                self.cfg["providers"] = providers_list
                config.save(self.cfg)
            log(f"已更新 provider {provider_id}")
            return True
        except Exception as e:
            log(f"更新 provider 失败: {e}")
            return False

    def get_provider(self, provider_id: str) -> Optional[Dict[str, Any]]:
        """获取单个 provider 配置。"""
        for p in self.cfg.get("providers", []):
            if p["id"] == provider_id:
                return p
        return None

    # ---- CDP 相关 ----

    def launch_cdp_chrome(self, port: int = 9222, url: str = "") -> Dict[str, Any]:
        """启动调试 Chrome。"""
        chrome = find_chrome()
        if not chrome:
            return {"success": False, "error": "找不到 Chrome，请安装或手动启动"}
        
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
        profile = os.path.join(base, "token_view", "chrome_profile")
        os.makedirs(os.path.dirname(profile), exist_ok=True)
        
        args = [
            chrome,
            f"--remote-debugging-port={port}",
            f"--user-data-dir={profile}",
            "--remote-allow-origins=*",
            "--no-first-run",
            "--no-default-browser-check",
            url or "about:blank"
        ]
        
        try:
            subprocess.Popen(args)
            log(f"已启动 CDP Chrome: port={port} url={url}")
            return {"success": True, "port": port}
        except Exception as e:
            log(f"启动 Chrome 失败: {e}")
            return {"success": False, "error": str(e)}

    # ---- 窗口管理 ----

    def toggle_on_top(self) -> bool:
        """切换窗口置顶状态（屏幕级别）。"""
        try:
            new_state = not self.cfg.get("always_on_top", True)
            self.cfg["always_on_top"] = new_state
            config.save(self.cfg)
            
            # macOS 特定：设置窗口级别
            import platform
            if platform.system() == 'Darwin':
                try:
                    from Cocoa import NSApplication
                    from Quartz import kCGFloatingWindowLevel, kCGNormalWindowLevel
                    
                    app = NSApplication.sharedApplication()
                    windows = app.windows()
                    if windows:
                        window = windows[0]
                        if new_state:
                            window.setLevel_(kCGFloatingWindowLevel)
                        else:
                            window.setLevel_(kCGNormalWindowLevel)
                except Exception as e:
                    log(f"macOS 窗口级别设置失败: {e}")
            
            # 通用方法
            if self.window:
                self.window.on_top = new_state
            
            log(f"屏幕置顶已{'开启' if new_state else '关闭'}")
            return new_state
        except Exception as e:
            log(f"切换置顶失败: {e}")
            return self.cfg.get("always_on_top", True)

    def set_opacity(self, opacity: float) -> bool:
        """设置窗口透明度。"""
        try:
            self.cfg["opacity"] = max(0.3, min(1.0, opacity))
            config.save(self.cfg)
            
            # macOS 特定：设置窗口透明度
            import platform
            if platform.system() == 'Darwin':
                try:
                    from Cocoa import NSApplication
                    
                    app = NSApplication.sharedApplication()
                    windows = app.windows()
                    if windows:
                        window = windows[0]
                        window.setAlphaValue_(self.cfg["opacity"])
                        log(f"macOS 窗口透明度已设置: {self.cfg['opacity']}")
                except Exception as e:
                    log(f"macOS 窗口透明度设置失败: {e}")
            
            return True
        except Exception as e:
            log(f"设置透明度失败: {e}")
            return False

    def set_geometry(self, x: int, y: int, w: int, h: int) -> bool:
        """保存窗口位置和大小。"""
        try:
            self.cfg["geometry"] = [x, y, w, h]
            config.save(self.cfg)
            return True
        except Exception as e:
            log(f"保存窗口位置失败: {e}")
            return False

    def get_geometry(self) -> Optional[List[int]]:
        """获取保存的窗口位置。"""
        return self.cfg.get("geometry")

    def resize_window_to_content(self, width: int, height: int) -> bool:
        """按前端实际内容尺寸收缩窗口，避免透明区域挡住下层应用。"""
        try:
            if not self.window:
                return {"ok": False}

            width = int(width or 0)
            keep_width = width <= 0
            if not keep_width:
                width = max(260, width)
            height = max(80, min(1200, int(height)))

            import platform
            if platform.system() == "Darwin" and self._resize_window_macos(width, height, keep_width):
                return True

            x = int(getattr(self.window, "x", 0) or 0)
            y = int(getattr(self.window, "y", 0) or 0)
            if keep_width:
                width = self._current_window_width()
            else:
                self.window.resize(width, height)
            if keep_width:
                self.window.resize(width, height)
            self.cfg["geometry"] = [x, y, width, height]
            config.save(self.cfg)
            log(f"窗口已按内容缩放: w={width}, h={height}")
            return True
        except Exception as e:
            log(f"按内容缩放窗口失败: {e}")
            return False

    def move_window_to_top(self, width: int = 0, height: int = 0) -> Dict[str, Any]:
        """把主窗口放大并移动到当前所在屏幕顶部。"""
        try:
            if not self.window:
                return {"ok": False}

            import platform
            import webview

            system = platform.system()
            if system == "Darwin":
                return self._move_window_to_top_macos(width, height)
            if system == "Windows":
                win_result = self._move_window_to_top_windows(height)
                if win_result.get("ok"):
                    return win_result

            x = int(getattr(self.window, "x", 0) or 0)
            y = int(getattr(self.window, "y", 0) or 0)
            w = int(getattr(self.window, "width", 0) or 0)
            h = max(80, int(height or getattr(self.window, "height", 0) or 0))
            screens = self._get_webview_screens(webview)
            screen = None

            if screens and w and h:
                cx = x + w // 2
                cy = y + h // 2
                for item in screens:
                    sx = self._screen_value(item, "x")
                    sy = self._screen_value(item, "y")
                    sw = self._screen_value(item, "width")
                    sh = self._screen_value(item, "height")
                    in_x = sx <= cx < sx + sw
                    in_y = sy <= cy < sy + sh
                    if in_x and in_y:
                        screen = item
                        break
                if screen is None:
                    screen = screens[0]

            if screen is not None:
                x, y, w, h = self._top_bar_geometry(screen, h)
            else:
                w = max(1200, int(width or w))
                x = 40
                y = 36

            self.window.resize(w, h)
            self.window.move(x, y)
            if w and h:
                self.cfg["geometry"] = [x, y, w, h]
                config.save(self.cfg)
            log(f"窗口已放大并移动到顶部: x={x}, y={y}, w={w}, h={h}")
            return {"ok": True, "width": w, "height": h}
        except Exception as e:
            log(f"移动窗口到顶部失败: {e}")
            return {"ok": False}

    def _resize_window_macos(self, width: int, height: int, keep_width: bool = False) -> bool:
        """macOS 窗口缩放，所有 NSWindow 几何操作都切到主线程。"""
        try:
            def work():
                native = getattr(self.window, "native", None)
                target_w = width
                if keep_width:
                    target_w = self._current_window_width()

                if native is not None:
                    frame = native.frame()
                    frame.size.width = target_w
                    frame.size.height = height
                    native.setFrame_display_(frame, True)
                else:
                    self.window.resize(target_w, height)

                x = int(getattr(self.window, "x", 0) or 0)
                y = int(getattr(self.window, "y", 0) or 0)
                self.cfg["geometry"] = [x, y, target_w, height]
                config.save(self.cfg)
                return target_w

            target_w = self._run_on_macos_main_thread(work)
            log(f"macOS 窗口已按内容缩放: w={target_w}, h={height}")
            return True
        except Exception as e:
            log(f"macOS 按内容缩放窗口失败: {e}")
            return False

    def _run_on_macos_main_thread(self, func, timeout: float = 3.0):
        """在 macOS 主线程同步执行窗口几何操作。"""
        from Foundation import NSThread

        if NSThread.isMainThread():
            return func()

        done = threading.Event()
        box = {}

        def wrapper():
            try:
                box["value"] = func()
            except Exception as e:
                box["error"] = e
            finally:
                done.set()

        from PyObjCTools import AppHelper
        AppHelper.callAfter(wrapper)
        if not done.wait(timeout):
            raise TimeoutError("等待 macOS 主线程执行窗口操作超时")
        if "error" in box:
            raise box["error"]
        return box.get("value")

    def _get_webview_screens(self, webview_module):
        """兼容不同 pywebview 版本的屏幕列表接口。"""
        screens = getattr(webview_module, "screens", [])
        if callable(screens):
            return screens()
        return screens or []

    def _current_window_width(self, default: int = 260) -> int:
        """读取窗口真实宽度，macOS 原生 frame 优先。"""
        try:
            native = getattr(self.window, "native", None)
            if native is not None:
                frame = native.frame()
                width = int(frame.size.width or 0)
                if width > 0:
                    return max(default, width)
        except Exception:
            pass
        return max(default, int(getattr(self.window, "width", 0) or 0))

    def _screen_value(self, screen, key: str, default: int = 0) -> int:
        """兼容 pywebview Screen 对象和 dict 两种形态。"""
        if isinstance(screen, dict):
            value = screen.get(key, default)
        else:
            value = getattr(screen, key, default)
        return int(value or default)

    def _top_bar_geometry(self, screen, height: int):
        """计算顶部条位置：当前屏幕居中，长度约为屏幕的 80%。"""
        sx = self._screen_value(screen, "x")
        sy = self._screen_value(screen, "y")
        sw = max(320, self._screen_value(screen, "width", 1200))
        sh = max(120, self._screen_value(screen, "height", 800))
        top_margin = 36
        target_w = max(320, int(sw * 0.8))
        target_h = max(80, min(int(height or 0), max(80, sh - top_margin)))
        target_x = sx + max(0, (sw - target_w) // 2)
        target_y = sy + top_margin
        return target_x, target_y, target_w, target_h

    def _move_window_to_top_macos(self, width: int = 0, height: int = 0):
        """macOS 路径：移动窗口到屏幕顶部。"""
        try:
            return self._run_on_macos_main_thread(
                lambda: self._move_window_to_top_macos_native(height)
            )
        except Exception as e:
            log(f"macOS 移动窗口到顶部失败: {e}")
            return {"ok": False, "error": str(e)}

    def _move_window_to_top_macos_native(self, height: int = 0):
        """macOS 原生移动窗口，避开 pywebview move/resize 偶发不生效。"""
        try:
            native = getattr(self.window, "native", None)
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
            current_h = int(frame.size.height or getattr(self.window, "height", 0) or 0)
            target_h = max(80, min(int(height or current_h), max(80, sh - top_margin)))
            target_x = int(visible.origin.x + (sw - target_w) / 2)
            target_y = int(visible.origin.y + sh - target_h - top_margin)

            frame.origin.x = target_x
            frame.origin.y = target_y
            frame.size.width = target_w
            frame.size.height = target_h
            native.setFrame_display_(frame, True)
            native.orderFrontRegardless()

            pywebview_y = int(visible.origin.y + top_margin)
            self.cfg["geometry"] = [target_x, pywebview_y, target_w, target_h]
            config.save(self.cfg)
            log(f"macOS 原生窗口已移动到顶部: x={target_x}, y={pywebview_y}, w={target_w}, h={target_h}")
            return {"ok": True, "width": target_w, "height": target_h}
        except Exception as e:
            log(f"macOS 原生移动窗口到顶部失败: {e}")
            return {"ok": False, "error": str(e)}

    def _window_hwnd_windows(self) -> int:
        """读取 Windows 原生窗口句柄。"""
        native = getattr(self.window, "native", None)
        for name in ("Handle", "handle", "hwnd"):
            value = getattr(native, name, None) if native is not None else None
            if value is None:
                value = getattr(self.window, name, None)
            if value is None:
                continue
            try:
                if hasattr(value, "ToInt64"):
                    return int(value.ToInt64())
                return int(value)
            except (TypeError, ValueError):
                continue
        return 0

    def _move_window_to_top_windows(self, height: int = 0) -> Dict[str, Any]:
        """Windows 路径：用 Win32 工作区计算当前屏幕顶部条。"""
        try:
            import ctypes
            from ctypes import wintypes

            hwnd = self._window_hwnd_windows()
            if not hwnd:
                return {"ok": False}

            user32 = ctypes.windll.user32
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

            work = info.rcWork
            sw = max(320, int(work.right - work.left))
            sh = max(120, int(work.bottom - work.top))
            target_w = max(320, int(sw * 0.8))
            target_h = max(80, min(int(height or getattr(self.window, "height", 0) or 0), sh))
            target_x = int(work.left + (sw - target_w) / 2)
            target_y = int(work.top)

            ok = user32.SetWindowPos(
                wintypes.HWND(hwnd),
                wintypes.HWND(HWND_TOPMOST),
                target_x,
                target_y,
                target_w,
                target_h,
                SWP_NOACTIVATE,
            )
            if not ok:
                return {"ok": False}

            self.cfg["geometry"] = [target_x, target_y, target_w, target_h]
            config.save(self.cfg)
            log(f"Windows 窗口已移动到顶部: x={target_x}, y={target_y}, w={target_w}, h={target_h}")
            return {"ok": True, "width": target_w, "height": target_h}
        except Exception as e:
            log(f"Windows 移动窗口到顶部失败: {e}")
            return {"ok": False, "error": str(e)}

    # ---- 模式切换 ----

    def set_compact(self, compact: bool) -> bool:
        """设置紧凑模式。"""
        try:
            self.cfg["compact"] = compact
            config.save(self.cfg)
            return True
        except Exception as e:
            log(f"设置紧凑模式失败: {e}")
            return False

    def set_dock(self, dock: bool) -> bool:
        """设置 Dock 模式。"""
        try:
            self.cfg["dock"] = dock
            config.save(self.cfg)
            return True
        except Exception as e:
            log(f"设置 Dock 模式失败: {e}")
            return False

    def set_refresh_interval(self, seconds: int) -> bool:
        """设置刷新间隔。"""
        try:
            self.cfg["refresh_interval"] = max(15, min(3600, seconds))
            config.save(self.cfg)
            return True
        except Exception as e:
            log(f"设置刷新间隔失败: {e}")
            return False

    def open_settings_window(self) -> bool:
        """打开设置窗口（在主窗口上层）。"""
        try:
            import webview
            import os
            
            current_dir = os.path.dirname(os.path.abspath(__file__))
            settings_html = os.path.join(current_dir, 'web', 'settings.html')
            
            # 创建设置窗口，传递 API，设置为置顶
            self._settings_window = webview.create_window(
                '设置 - Token 用量监控',
                settings_html,
                js_api=self,  # 传递 API 实例
                width=1200,
                height=400,
                resizable=True,
                on_top=True  # 在主窗口上层
            )
            
            log("设置窗口已打开")
            return True
        except Exception as e:
            log(f"打开设置窗口失败: {e}")
            return False

    def close_settings(self) -> bool:
        """关闭设置窗口。"""
        try:
            if hasattr(self, '_settings_window') and self._settings_window:
                self._settings_window.destroy()
                self._settings_window = None
                log("设置窗口已关闭")
            return True
        except Exception as e:
            log(f"关闭设置窗口失败: {e}")
            return False

    def quit_app(self) -> bool:
        """退出应用程序。"""
        try:
            # 保存配置
            config.save(self.cfg)

            # 销毁所有窗口
            if self.window:
                try:
                    self.window.destroy()
                except Exception:
                    pass
            if hasattr(self, '_settings_window') and self._settings_window:
                try:
                    self._settings_window.destroy()
                except Exception:
                    pass

            # Windows: destroy() → winforms.py Application.Exit() 自动退出事件循环
            # macOS: Cocoa 事件循环不会自动退出，必须强制 terminate
            import platform
            if platform.system() == 'Darwin':
                import os
                os._exit(0)

            return True
        except Exception as e:
            log(f"退出应用失败: {e}")
            return False
