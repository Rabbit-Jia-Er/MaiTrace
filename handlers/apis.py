"""跨插件 @API 实现：发说说 / 获取说说列表。

由 plugin.py 里 @API 装饰的方法调用，参数透传。
图片入参统一为 base64 字符串（跨进程 RPC 安全），内部 b64decode 为 bytes。
"""

from __future__ import annotations

import base64
import binascii
import logging
from ..utils import get_logger
from typing import Any, Dict, List, Optional

from ..services.cookie import renew_cookies
from ..services.feed_publish import send_feed
from ..services.qzone_api import create_qzone_api
from ..utils import peel_envelope

logger = get_logger(__name__)


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


def _decode_images(images: Optional[List[str]]) -> List[bytes]:
    """把 base64 字符串列表解为 bytes 列表。无效条目跳过。"""
    if not images:
        return []
    out: List[bytes] = []
    for idx, item in enumerate(images):
        if not item:
            continue
        try:
            out.append(base64.b64decode(item))
        except (binascii.Error, ValueError) as exc:
            logger.warning("images[%d] base64 解码失败已跳过: %s", idx, exc)
    return out


async def send_feed_api(
    plugin,
    message: str = "",
    images: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """发送说说。供其他插件 ctx.api.call('Rabbit-Jia-Er.MaiTrace.send_feed_api', ...) 调用。

    Args:
        message: 说说正文（必填）
        images: 图片列表，每项为 base64 字符串（不含 data:image/... 前缀）。可选。
    """
    if not message:
        return {"result": False, "message": "message 为空"}
    qzone = await _ensure_qzone(plugin)
    if qzone is None:
        return {"result": False, "message": "无法创建 QzoneAPI，cookie 可能不存在"}
    try:
        tid = await qzone.publish_emotion(message, _decode_images(images))
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


async def publish_topic_api(
    plugin,
    topic: str = "",
    current_activity: str = "",
) -> Dict[str, Any]:
    """完整发说说流程（其它插件调）：LLM 按 topic 生成正文 → 自动配图 → 发布。

    走的是与 ``@Tool send_feed`` / ``/zn <主题>`` 同一条 ``services.feed_publish.send_feed`` 链路：

    1. 刷 cookie（自适应顺序）
    2. ``topic == "custom"`` 时取 ``send.custom_qqaccount`` 的最新私聊内容；
       否则 LLM 按 ``send.prompt`` + 人格 + 历史说说生成（防重复）
    3. 按 ``image.*`` 配置自动配图（绘卷 AI 图 / 表情包 / 混合）
    4. 发布到空间并清理本地图

    Args:
        plugin: MaiTracePlugin 实例（由 plugin.py 的 @API 装饰器注入）。
        topic: 说说主题；传 ``"custom"`` 进入 custom 模式。空值兜底为 ``"随机"``。
        current_activity: 可选；非空时会拼到 prompt 末尾，让 LLM 把"当前在做什么"
            自然写进说说。Routine 模式调用时会传当前日程活动。

    Returns:
        Dict[str, Any]::

            {
                "result": bool,       # 是否最终发布成功
                "story": str,         # 成功时为说说正文，失败时为空串
                "message": str,       # 人类可读的状态描述（成功/失败原因）
            }
    """
    normalized_topic = (topic or "").strip() or "随机"
    success, story = await send_feed(
        plugin,
        normalized_topic,
        current_activity=(current_activity or "").strip(),
    )
    if not success:
        return {
            "result": False,
            "story": "",
            "message": f"发说说失败: {story}",
        }
    return {
        "result": True,
        "story": story,
        "message": f"说说发送成功（topic={normalized_topic}）",
    }
