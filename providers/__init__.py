"""用量查询 Provider 集合。

公开 API：
  - build(cfg)            根据 cfg["type"] 实例化对应 Provider
  - BaseProvider          所有 Provider 的基类
  - UsageData / UsageItem 数据模型
  - fmt_tokens(n)         token 数字格式化

支持的 type：
  - "zhipu"      智谱 GLM Coding Plan（CDP/cookie/API key 三模式）
  - "opencode"   OpenCode Go（CDP 模式）
  - "mimo"       小米 MiMo Token Plan（CDP 模式）

详见各 provider 子模块的 docstring。
"""
from .base import (
    BaseProvider,
    UsageData,
    UsageItem,
    fmt_tokens,
)
from .opencode import OpenCodeProvider
from .zhipu import ZhipuProvider
from .mimo import MimoProvider


def build(cfg: dict) -> BaseProvider:
    """Provider 工厂。type 未知时抛 ValueError。"""
    ptype = cfg.get("type")
    if ptype == "zhipu":
        return ZhipuProvider(cfg)
    if ptype == "opencode":
        return OpenCodeProvider(cfg)
    if ptype == "mimo":
        return MimoProvider(cfg)
    raise ValueError(f"未知 provider 类型: {ptype!r}")


__all__ = [
    "BaseProvider",
    "UsageData",
    "UsageItem",
    "fmt_tokens",
    "ZhipuProvider",
    "OpenCodeProvider",
    "MimoProvider",
    "build",
]
