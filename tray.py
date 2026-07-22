"""系统托盘图标管理。

用 pystray 创建托盘图标，支持：
  - 单击：显示/隐藏主窗口
  - 右键菜单：显示/隐藏、刷新、退出

使用方式：
  tray = TrayIcon(api)
  tray.start()   # 在子线程启动，不阻塞主线程
  tray.stop()    # 退出时清理
"""
import platform
import threading

from logger import log


class TrayIcon:
    """系统托盘图标。"""

    def __init__(self, api):
        self.api = api
        self._icon = None
        self._thread = None

    def _make_image(self):
        """托盘图标：优先用 assets/icon.png（与应用图标一致），
        找不到时回退到手绘蓝底白 T。"""
        import os
        from PIL import Image, ImageDraw
        icon_path = os.path.join(
            os.path.dirname(os.path.abspath(__file__)), 'assets', 'icon.png')
        if os.path.exists(icon_path):
            try:
                return Image.open(icon_path).convert('RGBA').resize(
                    (64, 64), Image.LANCZOS)
            except Exception as e:
                log(f"加载 assets/icon.png 失败，回退手绘图标: {e}")
        size = 64
        image = Image.new('RGBA', (size, size), (0, 0, 0, 0))
        draw = ImageDraw.Draw(image)
        draw.rounded_rectangle(
            [(6, 6), (size - 6, size - 6)],
            radius=16,
            fill=(59, 130, 246, 255),
        )
        draw.text(
            (size // 2, size // 2),
            "T",
            fill=(255, 255, 255, 255),
            anchor="mm",
        )
        return image

    def _on_show(self, icon=None, item=None):
        """显示/隐藏主窗口。"""
        window = self.api.window
        if window is None:
            return
        try:
            if platform.system() == "Windows":
                # Windows: 用 Win32 ShowWindowAsync 切换可见性
                import ctypes
                from api.screen import window_hwnd
                hwnd = window_hwnd(window)
                if hwnd:
                    user32 = ctypes.windll.user32
                    SW_HIDE = 0
                    SW_SHOWNOACTIVATE = 4
                    if user32.IsWindowVisible(hwnd):
                        user32.ShowWindowAsync(hwnd, SW_HIDE)
                        log("托盘：隐藏窗口")
                    else:
                        user32.ShowWindowAsync(hwnd, SW_SHOWNOACTIVATE)
                        user32.SetForegroundWindow(hwnd)
                        log("托盘：显示窗口")
            else:
                # macOS / Linux: 用 pywebview show/hide
                visible = getattr(window, '_tv_visible', True)
                if visible:
                    window.hide()
                    setattr(window, '_tv_visible', False)
                    log("托盘：隐藏窗口")
                else:
                    window.show()
                    setattr(window, '_tv_visible', True)
                    log("托盘：显示窗口")
        except Exception as e:
            log(f"托盘切换显示失败: {e}")

    def _on_refresh(self, icon=None, item=None):
        """触发刷新。"""
        window = self.api.window
        if window is not None:
            try:
                window.evaluate_js("if(typeof refresh==='function')refresh();")
                log("托盘：触发刷新")
            except Exception as e:
                log(f"托盘刷新失败: {e}")

    def _on_quit(self, icon=None, item=None):
        """真正退出程序。"""
        log("托盘：退出程序")
        if self._icon is not None:
            self._icon.stop()
        # 调用 force_quit 真正销毁窗口并退出
        if self.api is not None:
            try:
                self.api.force_quit()
            except Exception as e:
                log(f"托盘退出失败: {e}")

    def start(self):
        """在子线程启动托盘图标。"""
        try:
            import pystray
        except ImportError:
            log("pystray 未安装，托盘功能不可用（pip install pystray pillow）")
            self._icon = None
            return

        image = self._make_image()

        # 构建右键菜单
        menu = pystray.Menu(
            pystray.MenuItem("显示/隐藏", self._on_show, default=True),
            pystray.MenuItem("刷新", self._on_refresh),
            pystray.MenuItem("退出", self._on_quit),
        )

        self._icon = pystray.Icon(
            "TokenView",
            image,
            "Token 用量监控",
            menu,
        )
        # Windows: 把图标设为始终可见（不被折叠到"显示隐藏的图标"区域）
        # pystray 底层用 Shell_NotifyIcon，NIF_TIP 标志确保图标在通知区显示

        def _run():
            try:
                self._icon.run()
            except Exception as e:
                log(f"托盘运行失败: {e}")

        self._thread = threading.Thread(target=_run, daemon=True)
        self._thread.start()
        log("系统托盘已启动")

    @property
    def running(self) -> bool:
        """托盘是否成功启动。"""
        return self._icon is not None

    def stop(self):
        """停止托盘图标。"""
        if self._icon is not None:
            try:
                self._icon.stop()
            except Exception:
                pass
            self._icon = None
