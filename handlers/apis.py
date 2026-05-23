"""跨插件 @API 实现：发说说 / 获取说说列表。

由 plugin.py 里 @API 装饰的方法调用，参数透传。
"""

from __future__ import annotations

import logging
from typing import Any, Dict, List, Optional

from ..services.cookie import renew_cookies
from ..services.qzone_api import create_qzone_api
from ..utils import peel_envelope

logger = logging.getLogger(__name__)


async def _resolve_uin(ctx) -> str:
    try:
        value = await ctx.config.get("bot.qq_account", "")
    except Exception:
        return ""
    value = peel_envelope(value)
    if isinstance(value, dict):
        value = value.get("value", "")
    return str(value or "")


async def _ensure_qzone(plugin):
    """刷 cookie + 构造 QzoneAPI。"""
    uin = await _resolve_uin(plugin.ctx)
    if not uin:
        return None
    ok = await renew_cookies(
        plugin.ctx,
        host=plugin.config.plugin.http_host,
        port=plugin.config.plugin.http_port,
        napcat_token=plugin.config.plugin.napcat_token,
        uin=uin,
        methods=list(plugin.config.plugin.cookie_methods),
    )
    if not ok:
        return None
    return create_qzone_api(uin)


async def send_feed_api(
    plugin,
    message: str = "",
    images: Optional[List[bytes]] = None,
) -> Dict[str, Any]:
    """发送说说。供其他插件 ctx.api.call('Rabbit-Jia-Er.MaiTrace.send_feed_api', ...) 调用。"""
    if not message:
        return {"result": False, "message": "message 为空"}
    qzone = await _ensure_qzone(plugin)
    if qzone is None:
        return {"result": False, "message": "无法创建 QzoneAPI，cookie 可能不存在"}
    try:
        tid = await qzone.publish_emotion(message, images or [])
    except Exception as exc:
        logger.error("send_feed_api 发布异常: %s", exc, exc_info=True)
        return {"result": False, "message": f"发送说说失败: {exc}"}
    return {"result": True, "message": f"说说发送成功，tid={tid}"}


async def get_feeds_list_api(
    plugin,
    target_qq: str,
    num: int = 5,
) -> Dict[str, Any]:
    """获取指定 QQ 的说说列表。"""
    if not target_qq:
        return {"result": False, "message": "target_qq 为空"}
    qzone = await _ensure_qzone(plugin)
    if qzone is None:
        return {"result": False, "message": "无法创建 QzoneAPI，cookie 可能不存在"}
    try:
        feeds = await qzone.get_list(str(target_qq), int(num))
    except Exception as exc:
        logger.error("get_feeds_list_api 异常: %s", exc, exc_info=True)
        return {"result": False, "message": f"获取列表失败: {exc}"}
    if not feeds or (len(feeds) == 1 and feeds[0].get("error")):
        err = feeds[0].get("error", "未知") if feeds else "空列表"
        return {"result": False, "message": f"获取说说失败: {err}"}
    return {"result": True, "message": f"成功获取 {len(feeds)} 条说说", "data": feeds}
