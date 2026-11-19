"""Provider 增删改查（前端 ➜ Python 配置层）。

不直接调用 providers.build().fetch()，那是 core 的事。
这里只管 JSON 配置层：add/remove/update/get。
"""
from __future__ import annotations

from typing import Optional

import config

# 后端管理字段：不允许被 updates 里的空值覆盖——前端快照回传时这些字段
# 是页面加载时的旧值/空值，直接合并会擦掉后台刚刷新/提取的凭证
# （saveProvider 曾整快照回传擦掉 kimi token；saveAll 也犯过）。
# 注意：extract_provider_credentials 写入的是非空新凭证，不受影响；
# 用户要重置凭证走「提取凭证」或删除账号重建。
_PROTECTED_FIELDS = ("access_token", "refresh_token", "auth_token",
                     "org_id", "project_id", "cookie", "api_key")


def list_providers(cfg: dict) -> list[dict]:
    return list(cfg.get("providers", []))


def add(cfg: dict, ptype: str) -> dict:
    new_p = config.new_provider(ptype)
    with config.io_lock:
        providers_list = cfg.get("providers", [])
        providers_list.append(new_p)
        cfg["providers"] = providers_list
        config.save(cfg)
    return new_p


def remove(cfg: dict, provider_id: str) -> bool:
    with config.io_lock:
        providers_list = cfg.get("providers", [])
        cfg["providers"] = [p for p in providers_list
                            if p.get("id") != provider_id]
        config.save(cfg)
    # 同步清理 state.json 里被删 provider 的条目（state 只在 fetch 时覆写，
    # 否则旧条目会一直留着，让读 state 的工具以为 provider 还在）
    from . import state as state_mod
    known_ids = {p.get("id") for p in cfg.get("providers", []) if p.get("id")}
    state_mod.prune_providers(known_ids)
    return True


def update(cfg: dict, provider_id: str, updates: dict) -> bool:
    if not updates:
        return True
    with config.io_lock:
        providers_list = cfg.get("providers", [])
        for p in providers_list:
            if p.get("id") == provider_id:
                for k, v in updates.items():
                    # 凭证字段拒绝空值覆盖（快照擦除防线）
                    if k in _PROTECTED_FIELDS and not v and p.get(k):
                        continue
                    p[k] = v
                break
        cfg["providers"] = providers_list
        config.save(cfg)
    return True


def get(cfg: dict, provider_id: str) -> Optional[dict]:
    for p in cfg.get("providers", []):
        if p.get("id") == provider_id:
            return p
    return None
