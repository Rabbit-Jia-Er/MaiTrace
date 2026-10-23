"""从 SDK API 列表识别 QQ 适配器、别名与实际登录账号。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from maibot_sdk.context import PluginContext

from ..utils import get_global_str, get_logger

logger = get_logger(__name__)
_API_PREFIXES = ("adapter.snowluma.", "adapter.napcat.")


@dataclass
class QQAdapter:
    """同一插件的两组 API 别名合并后只保留一个提供方。"""

    plugin_id: str
    api_names: Dict[str, str] = field(default_factory=dict)
    api_timeouts: Dict[str, int] = field(default_factory=dict)

    async def call(self, ctx: PluginContext, name: str, **kwargs: Any) -> Any:
        # Host 的组件默认预算是 60 秒；SDK 代理默认 30 秒会先超时。
        # RPC 单独留出 5 秒回传余量，绝不把超时字段传进 OneBot 动作参数。
        return await ctx.call_capability(
            "api.call", timeout_ms=self.api_timeouts.get(name, 60000) + 5000,
            api_name=self.api_names[name], version="1", args=kwargs,
        )

    async def login_uin(self, ctx: PluginContext) -> str:
        result = await self.call(ctx, "system.get_login_info")
        if isinstance(result, dict) and "status" in result:
            result = onebot_data(result)
        if not isinstance(result, dict):
            raise RuntimeError(f"{self.plugin_id} 未返回 QQ 登录信息")
        uin = normalize_uin(result.get("user_id"))
        if not uin:
            raise RuntimeError(f"{self.plugin_id} 未返回真实 QQ 号")
        return uin


def normalize_uin(value: Any) -> str:
    """空间只接受正数 QQ 号，官方机器人的 OpenID 不能作为 uin。"""
    text = str(value or "").strip()
    if not text.isascii() or not text.isdecimal():
        return ""
    return text.lstrip("0")


def onebot_data(result: Any) -> Dict[str, Any]:
    """解包适配器业务响应；SDK 已经剥去 Host 的 RPC 外层。"""
    if not isinstance(result, dict):
        raise RuntimeError("适配器未返回 OneBot 响应对象")
    if result.get("status") != "ok" or result.get("retcode", 0) != 0:
        error = result.get("wording") or result.get("message") or result.get("error") or "动作未成功"
        raise RuntimeError(f"适配器动作失败: {error}")
    data = result.get("data")
    if not isinstance(data, dict):
        raise RuntimeError("适配器响应缺少 data 对象")
    return data


async def discover_qq_adapters(
    ctx: PluginContext,
    priority: Optional[List[str]] = None,
    *,
    cached_id: str = "",
) -> List[QQAdapter]:
    """只调用实际注册的 v1 API，优先级支持完整插件 ID 或后缀。"""
    entries = await ctx.api.list()
    if not isinstance(entries, list):
        raise RuntimeError("适配器 API 列表查询失败，预期为 API 对象列表")
    adapters: Dict[str, QQAdapter] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            raise RuntimeError("适配器 API 列表包含非对象条目")
        if entry.get("enabled") is False or entry.get("public") is False:
            continue
        if str(entry.get("version") or "1") != "1":
            continue
        name = str(entry.get("name") or "")
        prefix = next((value for value in _API_PREFIXES if name.startswith(value)), "")
        plugin_id = str(entry.get("plugin_id") or "").strip()
        if not prefix or not plugin_id:
            continue
        adapter = adapters.setdefault(plugin_id, QQAdapter(plugin_id))
        short_name = name.removeprefix(prefix)
        # 当前统一适配器注册两组别名；优先使用 SnowLuma 别名，绝不重复调用。
        if short_name not in adapter.api_names or prefix == _API_PREFIXES[0]:
            adapter.api_names[short_name] = f"{plugin_id}.{name}"
            metadata = entry.get("metadata") or {}
            adapter.api_timeouts[short_name] = int(metadata.get("timeout_ms") or 60000)

    preferences = [str(value).strip() for value in (priority or []) if str(value).strip()]

    def rank(adapter: QQAdapter) -> tuple[int, bool]:
        index = next((i for i, value in enumerate(preferences)
                      if adapter.plugin_id == value or adapter.plugin_id.endswith(f".{value}")), len(preferences))
        # 成功缓存只在相同偏好中优先，不能覆盖用户刚修改的显式优先级。
        return index, adapter.plugin_id != cached_id

    return sorted(adapters.values(), key=rank)


async def resolve_bot_uin(
    ctx: PluginContext,
    priority: Optional[List[str]] = None,
    *,
    adapters: Optional[List[QQAdapter]] = None,
) -> str:
    """优先取实际登录账号；无可用 QQ 适配器时使用宿主备用 QQ 号。"""
    uin, _adapter = await resolve_qq_account(ctx, priority, adapters=adapters)
    return uin


async def resolve_qq_account(
    ctx: PluginContext,
    priority: Optional[List[str]] = None,
    *,
    adapters: Optional[List[QQAdapter]] = None,
) -> Tuple[str, Optional[QQAdapter]]:
    """一次只读查询确定账号及提供方，所有空间操作遵守同一适配器优先级。"""
    candidates = adapters if adapters is not None else await discover_qq_adapters(ctx, priority)
    for adapter in candidates:
        if "system.get_login_info" not in adapter.api_names:
            continue
        try:
            return await adapter.login_uin(ctx), adapter
        except Exception as exc:
            logger.warning("读取 %s 登录账号失败: %s", adapter.plugin_id, exc)
    return normalize_uin(await get_global_str(ctx, "bot.qq_account", "")), None
