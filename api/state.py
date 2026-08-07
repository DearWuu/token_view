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


def read_state() -> dict:
    """读已有 state.json；不存在或损坏返回空 dict（不抛异常）。"""
    path = state_file_path()
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def prune_providers(known_ids: set[str]) -> bool:
    """清理 state.json 中 cfg 已不存在的 provider 条目。

    被删 provider 的旧条目会一直留在 state.json（因为 state 只在 fetch 时被
    整文件覆写，且新 fetch 列表里没有它）—— 这会让读 state 的 companion 工具
    以为 provider 还在。启动和删除 provider 时各跑一次，确保 state 与 cfg 一致。

    返回是否有改动（用于日志/避免无谓写盘）。
    """
    payload = read_state()
    providers = payload.get("providers") or []
    if not providers:
        return False
    kept = [p for p in providers if p.get("id") in known_ids]
    if len(kept) == len(providers):
        return False
    payload["providers"] = kept
    payload["ts"] = time.time()
    payload["ts_iso"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    write_atomic(state_file_path(), payload)
    return True
