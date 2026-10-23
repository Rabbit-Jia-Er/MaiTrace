"""空间发布连接：SnowLuma 原生 API，NapCat 等客户端通过 Cookie HTTP。"""

from __future__ import annotations

from datetime import datetime
from typing import Any, List, Optional, Protocol
import asyncio
import base64

from maibot_sdk.context import PluginContext

from ..utils import get_logger
from .adapter import QQAdapter, discover_qq_adapters, onebot_data, resolve_bot_uin, resolve_qq_account
from .cookie import renew_cookies
from .qzone_api import PublicationState, PublicationUncertainError, QzoneAPI, create_qzone_api

logger = get_logger(__name__)


class QzonePublisher(Protocol):
    uin: str

    async def publish_emotion(
        self, content: str, images: Optional[List[bytes]] = None,
        *, publication: Optional[PublicationState] = None,
    ) -> str: ...

    async def get_send_history(self, num: int) -> str: ...


class AdapterQzonePublisher:
    """已通过只读探测确认连接 SnowLuma 的发布器。"""

    def __init__(self, ctx: PluginContext, adapter: QQAdapter, uin: str) -> None:
        self.ctx = ctx
        self.adapter = adapter
        self.uin = uin

    async def publish_emotion(
        self, content: str, images: Optional[List[bytes]] = None,
        *, publication: Optional[PublicationState] = None,
    ) -> str:
        state = publication if publication is not None else PublicationState()
        if not content:
            raise ValueError("说说正文为空")
        params: dict[str, Any] = {"content": content, "ugc_right": 1}
        if images:
            if any(not isinstance(image, bytes) or not image for image in images):
                raise ValueError("说说包含空图或无效图片")
            params["images"] = ["base64://" + base64.b64encode(image).decode("ascii") for image in images]

        # SnowLuma 在一次动作里串行上传全部图片再发布。RPC 发出后无法获知内部阶段，
        # 超时、断连及取消均按结果不明处理，不能切换 HTTP 或另一个适配器重发。
        state.stage = "submitting"
        try:
            result = await self.adapter.call(self.ctx, "qzone.send_qzone_msg", params=params)
        except asyncio.CancelledError:
            state.stage = "uncertain"
            raise
        except Exception as exc:
            state.stage = "uncertain"
            raise PublicationUncertainError("适配器空间发布结果待核对，请检查空间后再决定是否重发") from exc

        try:
            data = onebot_data(result)
            tid = data.get("tid")
            if (isinstance(tid, bool) or not isinstance(tid, (str, int))
                    or not str(tid).strip() or str(tid).strip() == "0" or isinstance(tid, int) and tid < 0):
                raise ValueError("发布响应缺少有效动态 ID")
        except (RuntimeError, ValueError) as exc:
            state.stage = "uncertain"
            raise PublicationUncertainError(f"适配器未确认空间发布结果，请核对空间记录: {exc}") from exc
        state.stage = "confirmed"
        state.tid = str(tid).strip()
        return state.tid

    async def get_send_history(self, num: int) -> str:
        """从同一账号的空间读取历史，保留主题生成时的防重复上下文。"""
        if num <= 0:
            return ""
        result = await self.adapter.call(
            self.ctx, "qzone.get_qzone_msg_list", params={"target_uin": int(self.uin), "num": min(num, 100)},
        )
        feeds = onebot_data(result).get("msglist")
        if not isinstance(feeds, list) or any(not isinstance(feed, dict) for feed in feeds):
            raise RuntimeError("适配器空间历史响应缺少 msglist 对象列表")
        history = "==================="
        for feed in feeds[:num]:
            created_time = datetime.fromtimestamp(int(feed["time"])).isoformat(sep=" ", timespec="seconds")
            history += (f"\n时间：'{created_time}'。\n说说内容：'{feed['content']}'"
                        f"\n图片：'{feed.get('images', [])}'\n===================")
        return history


async def find_adapter_publisher(
    ctx: PluginContext, adapter: QQAdapter, uin: str,
) -> Optional[AdapterQzonePublisher]:
    """先只读确认真实客户端，API 前缀与插件 ID 都不代表当前连接的能力。"""
    required = {"qzone.send_qzone_msg", "system.get_version_info"}
    if not required.issubset(adapter.api_names):
        return None
    try:
        version = onebot_data(await adapter.call(ctx, "system.get_version_info", params={}))
        app_name = str(version.get("app_name") or version.get("app") or "").strip().lower()
        if "snowluma" not in app_name:
            return None
    except Exception as exc:
        logger.warning("探测 %s 空间发布能力失败: %s", adapter.plugin_id, exc)
        return None
    logger.info("空间发布使用 %s 的 SnowLuma API，QQ=%s", adapter.plugin_id, uin)
    return AdapterQzonePublisher(ctx, adapter, uin)


async def ensure_http_qzone(plugin: Any, *, uin: Optional[str] = None) -> Optional[QzoneAPI]:
    """读取实际 QQ 账号、按配置刷新 Cookie，再构造 HTTP 空间客户端。"""
    if uin is None:
        uin = await resolve_bot_uin(plugin.ctx, list(plugin.config.plugin.adapter_priority))
    if not uin:
        raise RuntimeError("未找到真实 QQ 账号；请连接 SnowLuma/NapCat 或配置 bot.qq_account 和空间 Cookie 登录")
    config = plugin.config.plugin
    if not await renew_cookies(
        plugin.ctx, storage=plugin.persistence,
        host=config.http_host, port=config.http_port, http_token=config.http_token,
        uin=uin, methods=list(config.cookie_methods), adapter_priority=list(config.adapter_priority),
    ):
        return None
    return await asyncio.to_thread(create_qzone_api, uin, storage=plugin.persistence)


async def create_qzone_publisher(plugin: Any) -> Optional[QzonePublisher]:
    adapters = await discover_qq_adapters(plugin.ctx, list(plugin.config.plugin.adapter_priority))
    uin, adapter = await resolve_qq_account(plugin.ctx, adapters=adapters)
    if adapter is not None:
        publisher = await find_adapter_publisher(plugin.ctx, adapter, uin)
        if publisher is not None:
            return publisher
    return await ensure_http_qzone(plugin, uin=uin)
