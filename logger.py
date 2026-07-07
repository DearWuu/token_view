"""简单日志：写文件，用于调试 GUI 里的异步登录/抓取流程。

日志文件：%APPDATA%/token_view/debug.log
"""
import os
import time


def log_path() -> str:
    base = os.environ.get("APPDATA") or os.path.expanduser("~")
    return os.path.join(base, "token_view", "debug.log")


def log(msg: str):
    line = f"[{time.strftime('%H:%M:%S')}] {msg}"
    try:
        with open(log_path(), "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except OSError:
        pass
    # print(line, flush=True)
