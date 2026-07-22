"""用量查询 Provider 集合。

公开 API：
  - build(cfg)            根据 cfg["type"] 实例化对应 Provider
  - provider_class(t)     根据 type 返回 Provider 类（不实例化）
  - BaseProvider          所有 Provider 的基类
  - UsageData / UsageItem 数据模型
  - fmt_tokens(n)         token 数字格式化
  - CDPError 等           凭证提取 / CDP 通讯异常

支持的 type：
  - "zhipu"       智谱 GLM Coding Plan（凭证直连/CDP/API key 三模式）
  - "kimi"        Kimi 订阅额度（凭证直连/CDP）
  - "opencode"    OpenCode Go（凭证直连/CDP）
  - "mimo"        小米 MiMo Token Plan（凭证直连/CDP）
  - "volcengine"  火山引擎 Ark Agent Plan（凭证直连/CDP）

详见各 provider 子模块的 docstring。
"""
from .base import (
    BaseProvider,
    UsageData,
    UsageItem,
    fmt_tokens,
)
from .cdp import CDPError, CDPEvalError, CDPNotConnected, CDPPageNotFound
from .kimi import KimiProvider
from .opencode import OpenCodeProvider
from .zhipu import ZhipuProvider
from .mimo import MimoProvider
from .volcengine import VolcEngineProvider


_CLASSES = {
    "zhipu": ZhipuProvider,
    "kimi": KimiProvider,
    "opencode": OpenCodeProvider,
    "mimo": MimoProvider,
    "volcengine": VolcEngineProvider,
}


def provider_class(ptype: str) -> type[BaseProvider]:
    """按 type 返回 Provider 类。type 未知时抛 ValueError。"""
    cls = _CLASSES.get(ptype)
    if cls is None:
        raise ValueError(f"未知 provider 类型: {ptype!r}")
    return cls


def build(cfg: dict) -> BaseProvider:
    """Provider 工厂。"""
    return provider_class(cfg.get("type"))(cfg)


__all__ = [
    "BaseProvider",
    "UsageData",
    "UsageItem",
    "fmt_tokens",
    "CDPError",
    "CDPEvalError",
    "CDPNotConnected",
    "CDPPageNotFound",
    "ZhipuProvider",
    "KimiProvider",
    "OpenCodeProvider",
    "MimoProvider",
    "VolcEngineProvider",
    "provider_class",
    "build",
]
