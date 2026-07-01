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
    return {"success": True, "port": port}
