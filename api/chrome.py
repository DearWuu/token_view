"""Chrome 可执行文件查找 + CDP Chrome 启动。

CDP Chrome 是必选启动方式：
  - 必须带 --remote-debugging-port=<port>
  - 必须带 --remote-allow-origins=*（否则 CDP WebSocket 403）
  - 用独立 --user-data-dir，不污染用户主 Chrome
"""
from __future__ import annotations

import os
import platform
import subprocess
from pathlib import Path

from logger import log


def find_chrome() -> str:
    """跨平台查找 Chrome 可执行文件路径。找不到返回空串。"""
    system = platform.system()

    if system == "Darwin":
        candidates = [
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            os.path.expanduser("~/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
        ]
        for p in candidates:
            if os.path.exists(p):
                return p

    if system == "Windows":
        bases = [
            os.environ.get("PROGRAMFILES", r"C:\Program Files"),
            os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)"),
            os.environ.get("LOCALAPPDATA", ""),
        ]
        for base in bases:
            if not base:
                continue
            p = Path(base) / "Google" / "Chrome" / "Application" / "chrome.exe"
            if p.exists():
                return str(p)
    else:
        for name in ("google-chrome", "google-chrome-stable",
                     "chromium-browser", "chromium"):
            try:
                r = subprocess.run(["which", name], capture_output=True, text=True)
            except OSError:
                continue
            found = r.stdout.strip()
            if found:
                return found
    return ""


def default_profile_dir() -> Path:
    """CDP Chrome 的独立 user-data-dir。"""
    base = os.environ.get("APPDATA") or os.path.expanduser("~")
    p = Path(base) / "token_view" / "chrome_profile"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _wait_target_page(port: int, url: str, timeout: float = 10) -> bool:
    """轮询调试端口直到目标 url 的页面出现（确认真 Chrome 绑定了端口）。

    IPv4 回环被其他程序（如 Electron 应用）抢占时，Chrome 会退而绑 IPv6，
    两个地址都要查。
    """
    import time

    import requests

    keyword = url.split("?")[0].split("//")[-1]
    bases = (f"http://127.0.0.1:{port}", f"http://[::1]:{port}")
    deadline = time.time() + timeout
    while time.time() < deadline:
        for base in bases:
            try:
                targets = requests.get(base + "/json", timeout=3).json()
            except Exception:  # noqa: BLE001
                continue
            if any(t.get("type") == "page" and keyword in (t.get("url") or "")
                   for t in targets):
                return True
        time.sleep(0.5)
    return False


def launch_cdp_chrome(port: int = 9222, url: str = "") -> dict:
    """启动一个独立的调试 Chrome。

    返回 {"success": True/False, "port": ..., "error": ...}
    """
    chrome = find_chrome()
    if not chrome:
        return {"success": False, "error": "找不到 Chrome，请安装或手动启动"}

    args = [
        chrome,
        f"--remote-debugging-port={port}",
        f"--user-data-dir={default_profile_dir()}",
        "--remote-allow-origins=*",
        "--no-first-run",
        "--no-default-browser-check",
        url or "about:blank",
    ]
    try:
        subprocess.Popen(args)
    except OSError as e:
        log(f"启动 Chrome 失败: {e}")
        return {"success": False, "error": str(e)}

    log(f"已启动 CDP Chrome: port={port} url={url}")

    # 启动后验证目标页面真的出现在调试端口上——
    # 端口被其他程序（如 Electron 应用）抢占时 Chrome 启动看似成功，
    # 但 CDP 连过去的是别的程序，提取凭证/兜底会打到错误目标
    if url and not _wait_target_page(port, url):
        err = (f"调试端口 {port} 上未出现目标页面：端口可能被其他程序"
               f"（如 Electron 应用）抢占，请关闭占用程序或在设置里更换 CDP 端口")
        log(f"CDP Chrome 启动验证失败: {err}")
        return {"success": False, "error": err}
    return {"success": True, "port": port}
