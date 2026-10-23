"""主程序全局配置读取的统一 helper。

所有 ``ctx.config.get("personality.*", "bot.*", ...)`` 调用集中走这里，
当前 SDK 直接返回配置值；查询失败明确报错，默认值只用于缺少配置项。

约定：

- ``get_global``：返回原始值（任意类型），不存在时返回 ``default``
- ``get_global_str`` / ``get_global_list`` / ``get_global_float``：在 ``get_global`` 上
  再做一层类型规范化
"""

from __future__ import annotations

from typing import Any, List


async def get_global(ctx, key: str, default: Any = None) -> Any:
    """读取主程序全局配置，保留当前 SDK 的返回值。

    Args:
        ctx: 插件上下文。
        key: 点分隔 key，如 ``"personality.personality"``、``"bot.qq_account"``。
        default: 不存在时返回。

    Returns:
        Any: SDK 返回的原始值。
    """
    value = await ctx.config.get(key, default)
    if isinstance(value, dict) and value.get("success") is False:
        raise RuntimeError(f"读取主程序配置 {key} 失败: {value.get('error', '未知错误')}")
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
