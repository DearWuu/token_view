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

            width = max(260, int(width))
            height = max(80, min(1200, int(height)))

            import platform
            if platform.system() == "Darwin" and self._resize_window_macos(width, height):
                return True

            self.window.resize(width, height)
            x = int(getattr(self.window, "x", 0) or 0)
            y = int(getattr(self.window, "y", 0) or 0)
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

            if platform.system() == "Darwin":
                mac_result = self._move_window_to_top_macos(width, height)
                if mac_result:
                    return mac_result

            x = int(getattr(self.window, "x", 0) or 0)
            y = int(getattr(self.window, "y", 0) or 0)
            w = int(getattr(self.window, "width", 0) or 0)
            h = max(80, int(height or getattr(self.window, "height", 0) or 0))
            screens = webview.screens
            screen = None

            if screens and w and h:
                cx = x + w // 2
                cy = y + h // 2
                for item in screens:
                    in_x = item.x <= cx < item.x + item.width
                    in_y = item.y <= cy < item.y + item.height
                    if in_x and in_y:
                        screen = item
                        break
                if screen is None:
                    screen = screens[0]

            if screen is not None:
                margin = min(40, max(0, screen.width // 30))
                top_margin = 36
                w = max(320, screen.width - margin * 2)
                h = min(h, max(80, screen.height - top_margin))
                x = screen.x + margin
                y = screen.y + top_margin
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

    def _resize_window_macos(self, width: int, height: int) -> bool:
        """macOS 窗口缩放 - 使用 pywebview 标准方法。"""
        try:
            # 直接使用 pywebview 的 resize 方法，避免线程问题
            self.window.resize(width, height)
            x = int(getattr(self.window, "x", 0) or 0)
            y = int(getattr(self.window, "y", 0) or 0)
            self.cfg["geometry"] = [x, y, width, height]
            config.save(self.cfg)
            log(f"macOS 窗口已按内容缩放: w={width}, h={height}")
            return True
        except Exception as e:
            log(f"macOS 按内容缩放窗口失败: {e}")
            return False

    def _move_window_to_top_macos(self, width: int = 0, height: int = 0):
        """macOS 路径：移动窗口到屏幕顶部。"""
        try:
            import webview
            
            # 使用 pywebview 的 screens 获取屏幕信息
            screens = webview.screens
            if not screens:
                return False
            
            # 获取当前窗口位置
            x = int(getattr(self.window, "x", 0) or 0)
            y = int(getattr(self.window, "y", 0) or 0)
            w = int(getattr(self.window, "width", 0) or 0)
            h = max(80, int(height or getattr(self.window, "height", 0) or 0))
            
            # 找到当前窗口所在的屏幕
            screen = None
            cx = x + w // 2
            cy = y + h // 2
            for item in screens:
                in_x = item.x <= cx < item.x + item.width
                in_y = item.y <= cy < item.y + item.height
                if in_x and in_y:
                    screen = item
                    break
            if screen is None:
                screen = screens[0]
            
            # 计算新位置和大小
            margin = min(40, max(0, screen.width // 30))
            top_margin = 36
            w = max(320, screen.width - margin * 2)
            h = min(h, max(80, screen.height - top_margin))
            x = screen.x + margin
            y = screen.y + top_margin
            
            # 使用 pywebview 的 move 和 resize 方法
            self.window.resize(w, h)
            self.window.move(x, y)
            
            self.cfg["geometry"] = [x, y, w, h]
            config.save(self.cfg)
            log(f"macOS 窗口已放大并移动到顶部: x={x}, y={y}, w={w}, h={h}")
            return {"ok": True, "width": w, "height": h}
        except Exception as e:
            log(f"macOS 移动窗口到顶部失败: {e}")
            return False

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

            # 销毁所有窗口，WinForms 事件循环会自动 Application.Exit()
            #（winforms.py:397-398 在最后一个实例销毁时调 _shutdown），
            # 避免 os._exit 跳过 .NET 清理导致 Error 1411。
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

            return True
        except Exception as e:
            log(f"退出应用失败: {e}")
            return False
