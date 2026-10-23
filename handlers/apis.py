"""跨插件 @API 实现：发说说 / 获取说说列表。

由 plugin.py 里 @API 装饰的方法调用，参数透传。
图片入参统一为 base64 字符串（跨进程 RPC 安全），内部 b64decode 为 bytes。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional
import base64
import binascii

from ..services.feed_publish import send_feed
from ..services.qzone_api import PublicationState, PublicationUncertainError
from ..services.qzone_activity import record_activity
from ..services.qzone_publisher import create_qzone_publisher, ensure_http_qzone
from ..utils import get_logger

logger = get_logger(__name__)


def _decode_images(images: Optional[List[str]]) -> List[bytes]:
    """把图片严格解码。无效图片会阻止发布，不能悄悄丢图。"""
    if not images:
        return []
    out: List[bytes] = []
    for idx, item in enumerate(images):
        if not item:
            raise ValueError(f"images[{idx}] 为空")
        try:
            decoded = base64.b64decode(item, validate=True)
        except (binascii.Error, TypeError, ValueError) as exc:
            raise ValueError(f"images[{idx}] 不是有效 base64 图片") from exc
        if not decoded:
            raise ValueError(f"images[{idx}] 解码结果为空")
        out.append(decoded)
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

    此接口只发布调用方准备好的正文和图片，绝不调用配图服务。
    绘卷 AutoSelfieTask 会调用它；不得在此引入 require_image 或生图重试。
    """
    if not message:
        return {"result": False, "message": "message 为空"}
    try:
        decoded_images = _decode_images(images)
    except ValueError as exc:
        return {"result": False, "message": f"未发送说说: {exc}"}
    state = PublicationState()
    try:
        qzone = await create_qzone_publisher(plugin)
        if qzone is None:
            return {"result": False, "message": "无法连接 QQ 空间，Cookie 可能不存在"}
        tid = await qzone.publish_emotion(message, decoded_images, publication=state)
    except PublicationUncertainError as exc:
        logger.error("send_feed_api 结果待核对: %s", exc)
        return {"result": False, "uncertain": True, "message": str(exc)}
    except Exception as exc:
        logger.error("send_feed_api 发布异常: %s", exc, exc_info=True)
        return {"result": False, "uncertain": state.uncertain, "message": f"发送说说失败: {exc}"}
    if not tid:
        return {"result": False, "message": "QQ 空间未返回发布成功的动态 ID"}
    await record_activity(plugin, "publish", fid=tid, text=message)
    return {"result": True, "message": f"说说发送成功，tid={tid}"}


async def get_feeds_list_api(
    plugin,
    target_qq: str,
    num: int = 5,
    filter: bool = False,
) -> Dict[str, Any]:
    """获取指定 QQ 的说说列表。"""
    if not target_qq:
        return {"result": False, "message": "target_qq 为空"}
    try:
        qzone = await ensure_http_qzone(plugin)
        if qzone is None:
            return {"result": False, "message": "无法创建 QzoneAPI，cookie 可能不存在"}
        feeds = await qzone.get_list(str(target_qq), int(num), filter_commented=filter)
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
    activity_type: str = "",
    outfit: str = "",
) -> Dict[str, Any]:
    """完整发说说流程（其它插件调）：LLM 按 topic 生成正文 → 自动配图 → 发布。

    走的是与 ``@Tool send_feed`` / ``/zn <主题>`` 同一条 ``services.feed_publish.send_feed`` 链路：

    1. 探测实际客户端，选择 SnowLuma 空间 API 或 Cookie HTTP
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
    publication = PublicationState()
    success, story = await send_feed(
        plugin,
        normalized_topic,
        current_activity=(current_activity or "").strip(),
        activity_type=activity_type,
        outfit=outfit,
        publication=publication,
    )
    if not success:
        return {
            "result": False,
            "story": "",
            "message": f"发说说失败: {story}",
            "uncertain": publication.uncertain,
        }
    return {
        "result": True,
        "story": story,
        "message": f"说说发送成功（topic={normalized_topic}）",
    }
