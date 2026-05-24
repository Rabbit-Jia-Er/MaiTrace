"""主程序全局配置读取的统一 helper。

所有 ``ctx.config.get("personality.*", "bot.*", ...)`` 调用集中走这里，
统一处理 envelope（``peel_envelope``）和异常兜底，避免每个 service 都自己写一遍。

约定：

- ``get_global``：返回原始值（任意类型），异常时返回 ``default``
- ``get_global_str`` / ``get_global_list`` / ``get_global_float``：在 ``get_global`` 上
  再做一层类型规范化
"""

from __future__ import annotations

import logging
from ._logging import get_logger
from typing import Any, List

from ._envelope import peel_envelope

logger = get_logger(__name__)


async def get_global(ctx, key: str, default: Any = None) -> Any:
    """读主程序全局配置，剥 envelope。

    Args:
        ctx: 插件上下文。
        key: 点分隔 key，如 ``"personality.personality"``、``"bot.qq_account"``。
        default: 异常 / 不存在时返回。

    Returns:
        Any: envelope 剥离后的原始值。
    """
    try:
        value = await ctx.config.get(key, default)
    except Exception as exc:
        logger.warning("ctx.config.get(%s) 异常: %s", key, exc)
        return default
    value = peel_envelope(value)
    # 兼容 host 偶发返回的 {"value": ...} 包装
    if isinstance(value, dict) and "value" in value and "success" not in value:
        value = value.get("value", default)
    return value


async def get_global_str(ctx, key: str, default: str = "") -> str:
    """读字符串配置。空值 / None 兜底为 default。"""
    value = await get_global(ctx, key, default)
    if value is None:
        return default
    return str(value or default)


async def get_global_list(ctx, key: str) -> List[Any]:
    """读列表配置。非 list 兜底为空列表。"""
    value = await get_global(ctx, key, [])
    if isinstance(value, list):
        return value
    return []


async def get_global_float(ctx, key: str, default: float = 0.0) -> float:
    """读 float 配置。解析失败兜底 default。"""
    value = await get_global(ctx, key, default)
    try:
        return float(value)
    except (TypeError, ValueError):
        return default
