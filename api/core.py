"""Api 主类 —— pywebview 的 js_api 桥。

把下划线开头的内部模块（chrome/screen/window/providers/state/settings）
按 js_api 期望的方法名（无下划线）暴露给前端 JS 调用。
"""
from __future__ import annotations

import platform
import threading
from typing import Any, Optional

import config
import providers as provider_mod
from logger import log

from . import chrome, providers as providers_api, settings as settings_api, state
from . import screen as screen_helper
from . import window as window_helper


class Api:
    """pywebview JavaScript 桥对象。"""

    def __init__(self):
        self.cfg = config.load()
        self._lock = threading.Lock()
        self.window: Optional[Any] = None   # main.py 在启动后注入
        self._top_mode_width: Optional[int] = None  # 与 settings_api 共享运行时状态

    # ===================================================================
    # 数据相关
    # ===================================================================

    def get_usage(self) -> list[dict]:
        """获取所有 enabled provider 的最新用量（前端 ⇄ 状态文件 ⇄ companion 都用）。"""
        results = []
        for p in self.cfg.get("providers", []):
            if not p.get("enabled"):
                continue
            try:
                data = provider_mod.build(p).fetch()
                results.append(self._usage_to_dict(p, data))
            except Exception as e:  # noqa: BLE001
                log(f"Provider {p.get('type')} 错误: {e}")
                results.append(self._usage_to_dict(
                    p, provider_mod.UsageData(
                        provider_name=p.get("name") or p.get("type"),
                        status="error", error=str(e))))
        return results

    def get_config(self) -> dict:
        return {
            "providers": self.cfg.get("providers", []),
            "refresh_interval": self.cfg.get("refresh_interval", 60),
            "opacity": self.cfg.get("opacity", 0.92),
            "compact": self.cfg.get("compact", False),
            "dock": self.cfg.get("dock", False),
            "always_on_top": self.cfg.get("always_on_top", True),
            "geometry": self.cfg.get("geometry"),
            "screen": screen_helper.screen_layout(self.window)
            if self.window else {"x": 0, "y": 0, "width": 1200, "height": 800, "scale": 1.0},
        }

    def save_config(self, updates: dict) -> bool:
        try:
            with self._lock:
                self.cfg.update(updates)
                config.save(self.cfg)
            if "opacity" in updates:
                window_helper.set_opacity(self.window, updates["opacity"])
            log("配置已保存并应用")
            return True
        except OSError as e:
            log(f"保存配置失败: {e}")
            return False

    def collect_and_persist(self) -> list[dict]:
        """fetch 所有 provider 并把结果写 state.json。返回 fetch 结果。"""
        results = self.get_usage()
        try:
            state.write_state(results)
        except OSError as e:
            log(f"写 state.json 失败: {e}")
        return results

    # ===================================================================
    # Provider CRUD
    # ===================================================================

    def add_provider(self, ptype: str) -> dict:
        new_p = providers_api.add(self.cfg, ptype)
        log(f"已添加 {ptype} provider")
        return new_p

    def remove_provider(self, provider_id: str) -> bool:
        ok = providers_api.remove(self.cfg, provider_id)
        log(f"已删除 provider {provider_id}")
        return ok

    def update_provider(self, provider_id: str, updates: dict) -> bool:
        ok = providers_api.update(self.cfg, provider_id, updates)
        log(f"已更新 provider {provider_id}")
        return ok

    def get_provider(self, provider_id: str) -> Optional[dict]:
        return providers_api.get(self.cfg, provider_id)

    # ===================================================================
    # CDP Chrome 启动
    # ===================================================================

    def launch_cdp_chrome(self, port: int = 9222, url: str = "") -> dict:
        return chrome.launch_cdp_chrome(port, url)

    # ===================================================================
    # 窗口
    # ===================================================================

    def toggle_on_top(self) -> bool:
        new_state = not self.cfg.get("always_on_top", True)
        self.cfg["always_on_top"] = new_state
        config.save(self.cfg)
        if self.window is not None:
            window_helper.set_always_on_top(self.window, new_state)
        log(f"屏幕置顶已{'开启' if new_state else '关闭'}")
        return new_state

    def set_opacity(self, opacity: float) -> bool:
        settings_api.set_opacity(self.cfg, opacity)
        if self.window is not None:
            window_helper.set_opacity(self.window, opacity)
        return True

    def set_geometry(self, x: int, y: int, w: int, h: int) -> bool:
        window_helper.save_geometry(self.cfg, x, y, w, h)
        return True

    def get_geometry(self):
        return window_helper.get_geometry(self.cfg)

    def get_screen_layout(self) -> dict:
        return screen_helper.screen_layout(self.window)

    def set_top_mode(self, enabled: bool) -> bool:
        settings_api.set_top_mode(enabled)
        return True

    def resize_window_to_content(self, width: int, height: int) -> dict:
        return window_helper.resize_to_content(
            self.window, self._top_mode_width, width, height)

    def move_window_to_top(self, width: int = 0, height: int = 0) -> dict:
        result = window_helper.move_to_top(self.window, self.cfg, height)
        if result.get("ok") and result.get("width"):
            self._top_mode_width = result["width"]
        return result

    # ===================================================================
    # 模式 / 刷新
    # ===================================================================

    def set_compact(self, compact: bool) -> bool:
        self._top_mode_width = None
        return settings_api.set_compact(self.cfg, compact)

    def set_dock(self, dock: bool) -> bool:
        return settings_api.set_dock(self.cfg, dock)

    def set_refresh_interval(self, seconds: int) -> bool:
        return settings_api.set_refresh_interval(self.cfg, seconds)

    # ===================================================================
    # 设置窗口
    # ===================================================================

    def open_settings_window(self) -> bool:
        return settings_api.open_settings_window(self)

    def close_settings(self) -> bool:
        return settings_api.close_settings_window()

    def quit_app(self) -> bool:
        """保存配置 + 销毁所有窗口 + 退出。"""
        try:
            config.save(self.cfg)
        except OSError as e:
            log(f"退出时保存配置失败: {e}")

        if self.window is not None:
            try:
                self.window.destroy()
            except (OSError, RuntimeError):
                pass
        settings_api.close_settings_window()

        # Windows: destroy() 走 winforms.Application.Exit() 自动退
        # macOS: Cocoa 事件循环不会自动退，必须强 terminate
        if platform.system() == "Darwin":
            import os as _os
            _os._exit(0)
        return True

    # ===================================================================
    # 内部工具
    # ===================================================================

    @staticmethod
    def _usage_to_dict(p: dict, data: provider_mod.UsageData) -> dict:
        return {
            "id": p.get("id"),
            "name": data.provider_name,
            "type": p.get("type", ""),
            "level": data.plan_level,
            "status": data.status,
            "error": data.error,
            "fetched_at": data.fetched_at,
            "items": [
                {
                    "label": it.label,
                    "percent": it.used_percent,
                    "reset_at": it.reset_at,
                    "note": it.note,
                }
                for it in data.items
            ],
        }
