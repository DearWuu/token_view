"""状态文件协议 —— Headroom / claude-statusbar 风格。

把当前所有 provider 的最新数据原子写入
  %APPDATA%/token_view/state.json (Windows)
  ~/.token_view/state.json       (其他)

任何想消费用量数据的工具（菜单栏、状态栏 hook、IDE 插件、手机端推送…）
读这个 JSON 即可，不用自己重抓。

字段稳定版本号：schema=1
"""
from __future__ import annotations

import json
import os
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1


def state_file_path() -> Path:
    """跨平台的状态文件位置。"""
    base = os.environ.get("APPDATA") or os.path.expanduser("~")
    p = Path(base) / "token_view" / "state.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def build_payload(providers_data: list[dict]) -> dict:
    """构造对外的 state 协议。

    providers_data 元素是 core.collect_all() 返回的格式（dict），
    跟 UsageData.to_dict() 一致。
    """
    return {
        "schema": SCHEMA_VERSION,
        "ts": time.time(),
        "ts_iso": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "providers": providers_data,
    }


def write_atomic(path: Path, payload: dict) -> None:
    """原子写：写临时文件再 rename，避免读到半截 JSON。"""
    tmp_fd, tmp_path = tempfile.mkstemp(
        prefix="state-", suffix=".json.tmp", dir=str(path.parent))
    try:
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, path)
    except OSError:
        # 清理临时文件
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def write_state(providers_data: list[dict]) -> Path:
    """对外入口：把最新数据写到 state.json，返回路径。"""
    path = state_file_path()
    payload = build_payload(providers_data)
    write_atomic(path, payload)
    return path
