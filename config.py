"""配置存储。

把账号、刷新间隔、窗口位置等存到 %APPDATA%/token_view/config.json。
用普通 dict + JSON，方便用户增删账号。
"""
import json
import os
import uuid


def config_dir() -> str:
    # Windows 走 %APPDATA%，其它平台退回用户主目录
    base = os.environ.get("APPDATA") or os.path.expanduser("~")
    d = os.path.join(base, "token_view")
    os.makedirs(d, exist_ok=True)
    return d


def config_path() -> str:
    return os.path.join(config_dir(), "config.json")


DEFAULT = {
    "providers": [],          # 每项: {id,type,name,enabled, api_key/cookie, workspace_id...}
    "refresh_interval": 15,   # 秒
    "opacity": 0.92,          # 悬浮窗透明度 0~1
    "always_on_top": True,
    "geometry": None,         # [x, y, w, h]，记忆窗口位置
    "compact": False,         # 紧凑模式
}


def load() -> dict:
    p = config_path()
    cfg = dict(DEFAULT)
    if os.path.exists(p):
        try:
            with open(p, "r", encoding="utf-8") as f:
                cfg.update(json.load(f))
        except (json.JSONDecodeError, OSError):
            pass
    cfg.setdefault("providers", [])
    return cfg


def save(cfg: dict) -> None:
    tmp = dict(cfg)
    with open(config_path(), "w", encoding="utf-8") as f:
        json.dump(tmp, f, ensure_ascii=False, indent=2)


def new_provider(ptype: str) -> dict:
    """新建一个账号配置（带唯一 id）。"""
    base = {"id": uuid.uuid4().hex[:8], "type": ptype, "name": "", "enabled": True}
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
            # CDP 模式：连接用户已登录的调试 Chrome 抓页面，避免手动 cookie
            "cdp_enabled": True,
            "cdp_port": 9222,
            "cdp_url": "http://127.0.0.1:9222",
        })
    elif ptype == "mimo":
        base.update({
            "cookie": "",
            "name": "小米 MiMo",
            # CDP 模式：连接用户已登录的调试 Chrome 抓页面，避免手动 cookie
            "cdp_enabled": True,
            "cdp_port": 9222,
            "cdp_url": "http://127.0.0.1:9222",
        })
    return base
