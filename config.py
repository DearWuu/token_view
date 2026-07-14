"""配置存储。

外部 API 仍是 dict（保留对所有 .get() 调用方的兼容）。
内部用 pathlib + 原子写，避免读到半截 JSON。

位置：
  Windows  -> %APPDATA%/token_view/config.json
  其他平台 -> ~/.token_view/config.json
"""
from __future__ import annotations

import json
import os
import tempfile
import uuid
from pathlib import Path
from typing import Any


# 顶层 schema 描述（不是强制，只是文档 + default 兜底）
DEFAULT: dict[str, Any] = {
    "providers": [],          # 每项: {id, type, name, enabled, ...}
    "refresh_interval": 15,   # 秒
    "opacity": 0.92,          # 悬浮窗透明度 0~1
    "always_on_top": True,
    "theme": "dark",          # dark / light
    "geometry": None,         # [x, y, w, h]，记忆窗口位置
    "compact": False,         # 紧凑模式
    "dock": False,            # 顶部条模式
}


def config_dir() -> Path:
    base = os.environ.get("APPDATA") or os.path.expanduser("~")
    p = Path(base) / "token_view"
    p.mkdir(parents=True, exist_ok=True)
    return p


def config_path() -> Path:
    return config_dir() / "config.json"


def load() -> dict:
    """读 config.json 并 merge DEFAULT。

    损坏的 JSON 静默兜底成 DEFAULT（避免一次手滑毁掉所有配置）。
    """
    p = config_path()
    cfg = dict(DEFAULT)
    if p.exists():
        try:
            text = p.read_text(encoding="utf-8")
        except OSError:
            return cfg
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            return cfg
        if isinstance(data, dict):
            cfg.update(data)
    cfg.setdefault("providers", [])
    return cfg


def save(cfg: dict) -> None:
    """原子写：写临时文件再 rename。"""
    p = config_path()
    tmp_fd, tmp_path = tempfile.mkstemp(
        prefix="config-", suffix=".json.tmp", dir=str(p.parent))
    try:
        with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, p)
    except OSError:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


def new_provider(ptype: str) -> dict:
    """新建一个账号配置（带唯一 id）。

    每个 provider 仍是 dict 形态 —— 字段异构（zhipu 有 api_key/customer_id，
    opencode 有 workspace_id），硬要 dataclass 化反而啰嗦。
    """
    base: dict[str, Any] = {
        "id": uuid.uuid4().hex[:8],
        "type": ptype,
        "name": "",
        "enabled": True,
    }
    if ptype == "zhipu":
        base.update({
            "api_key": "",
            "base_url": "https://open.bigmodel.cn",
            "endpoint": "/api/monitor/usage/quota/limit",
            "cookie": "",
            "usage_url": "",
            "auth_token": "",
            "customer_id": "",
            # CDP 模式：连接用户已登录的调试 Chrome 抓团队用量，避开反爬
            "cdp_enabled": True,
            "cdp_port": 9222,
            "cdp_url": "http://127.0.0.1:9222",
        })
    elif ptype == "opencode":
        base.update({
            "cookie": "",
            "workspace_id": "",
            "name": "OpenCode Go",
            "cdp_enabled": True,
            "cdp_port": 9222,
            "cdp_url": "http://127.0.0.1:9222",
        })
    elif ptype == "mimo":
        base.update({
            "cookie": "",
            "name": "小米 MiMo",
            "cdp_enabled": True,
            "cdp_port": 9222,
            "cdp_url": "http://127.0.0.1:9222",
        })
    elif ptype == "volcengine":
        base.update({
            "cookie": "",
            "name": "火山 Ark",
            "cdp_enabled": True,
            "cdp_port": 9222,
            "cdp_url": "http://127.0.0.1:9222",
        })
    return base
