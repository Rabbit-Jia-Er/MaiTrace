"""发送说说服务。

把发文本/发图片/custom 模式（取私聊最新内容）这些流程合到一处。
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from ..utils import peel_envelope
from .cookie import renew_cookies
from .feed_image import collect_images_for_feed, cleanup_done_paths
from .llm_runner import LLMRunner
from .prompts import build_send_prompt
from .qzone_api import create_qzone_api

logger = logging.getLogger(__name__)


async def _get_global_str(ctx, key: str, default: str = "") -> str:
    try:
        value = await ctx.config.get(key, default)
    except Exception as exc:
        logger.warning("ctx.config.get(%s) 异常: %s", key, exc)
        return default
    value = peel_envelope(value)
    if isinstance(value, dict):
        value = value.get("value", default)
    return str(value or default)


async def _resolve_bot_uin(ctx) -> str:
    return await _get_global_str(ctx, "bot.qq_account", "")


async def _resolve_personality(ctx) -> tuple[str, str]:
    """返回 (personality, reply_style)。"""
    p = await _get_global_str(ctx, "personality.personality", "一个机器人")
    s = await _get_global_str(ctx, "personality.reply_style", "内容积极向上")
    return p, s


async def _renew_cookies(plugin) -> bool:
    uin = await _resolve_bot_uin(plugin.ctx)
    return await renew_cookies(
        plugin.ctx,
        host=plugin.config.plugin.http_host,
        port=plugin.config.plugin.http_port,
        napcat_token=plugin.config.plugin.napcat_token,
        uin=uin,
        methods=list(plugin.config.plugin.cookie_methods),
    )


async def _resolve_custom_content(plugin) -> Optional[str]:
    """custom 模式：取私聊最新一条非命令消息作为说说内容。"""
    cfg = plugin.config.send
    if not cfg.custom_qqaccount:
        logger.error("未配置 send.custom_qqaccount，custom 模式不可用")
        return None

    try:
        stream = await plugin.ctx.chat.get_stream_by_user_id(cfg.custom_qqaccount)
    except Exception as exc:
        logger.error("get_stream_by_user_id 失败: %s", exc)
        return None
    stream = peel_envelope(stream)
    if not isinstance(stream, dict):
        logger.error("ctx.chat 返回非 dict: %s", type(stream).__name__)
        return None
    stream_data = stream.get("stream", stream) if isinstance(stream, dict) else None
    chat_id = None
    if isinstance(stream_data, dict):
        chat_id = stream_data.get("stream_id") or stream_data.get("session_id")
    if not chat_id:
        logger.error("无法解析 stream_id：%s", stream)
        return None

    import time
    # 取最近 30 分钟内的消息
    end_time = time.time()
    start_time = end_time - 1800

    try:
        result = await plugin.ctx.message.get_by_time_in_chat(
            chat_id,
            start_time=str(start_time),
            end_time=str(end_time),
            limit=20,
            limit_mode="latest",
            filter_mai=False,
            filter_command=False,
        )
    except Exception as exc:
        logger.error("ctx.message.get_by_time_in_chat 异常: %s", exc)
        return None

    messages = peel_envelope(result)
    if isinstance(messages, dict):
        messages = messages.get("messages") or []
    if not isinstance(messages, list) or not messages:
        logger.error("custom 模式未获取到私聊消息")
        return None

    bot_uin = await _resolve_bot_uin(plugin.ctx)
    only_mai = plugin.config.send.custom_only_mai

    # 倒序找最新一条符合条件的消息
    for msg in reversed(messages):
        info = (msg.get("message_info") or {}) if isinstance(msg, dict) else {}
        user_info = info.get("user_info") or {}
        user_id = str(user_info.get("user_id", "") or "")
        text = (msg.get("processed_plain_text") or "").strip()
        if not text or text.startswith("/"):
            continue
        is_bot = (user_id == bot_uin)
        if only_mai and not is_bot:
            continue
        if not only_mai and is_bot:
            continue
        return text
    return None


async def send_feed(
    plugin,
    topic: str,
    *,
    current_activity: str = "",
) -> tuple[bool, str]:
    """主入口：根据 topic 生成内容并发到 QQ 空间。

    topic="custom" 时改走 _resolve_custom_content。
    Returns:
        (success, story) — 失败时 story 为错误消息。
    """
    # 1. cookie
    if not await _renew_cookies(plugin):
        return False, "更新 cookies 失败"

    # 2. QzoneAPI 实例
    uin = await _resolve_bot_uin(plugin.ctx)
    qzone = create_qzone_api(uin)
    if qzone is None:
        return False, "创建 QzoneAPI 失败，cookie 可能不存在"

    # 3. 决定说说内容
    if topic == "custom":
        custom_message = await _resolve_custom_content(plugin)
        if not custom_message:
            return False, "custom 模式取私聊内容失败"
        story = custom_message
        logger.info("custom 模式说说内容: %s", story)
    else:
        personality, expression = await _resolve_personality(plugin.ctx)
        prompt = await build_send_prompt(
            plugin, topic or "随机", personality, expression,
            qzone_api=qzone, current_activity=current_activity,
        )
        if plugin.config.models.show_prompt:
            logger.info("发说说 prompt: %s", prompt)

        runner = LLMRunner(plugin.ctx, plugin.config.models.text_model)
        success, story = await runner.generate(prompt, temperature=0.3)
        if not success or not story:
            return False, f"生成说说内容失败: {story}"
        logger.info("生成说说内容: %s", story)

    # 4. 收集图片
    personality_str, _ = await _resolve_personality(plugin.ctx)
    images, done_paths = await collect_images_for_feed(plugin, story, personality_str)

    # 5. 发布
    try:
        tid = await qzone.publish_emotion(story, images)
        logger.info("成功发送说说, tid=%s", tid)
        cleanup_done_paths(plugin, done_paths)
        return True, story
    except Exception as exc:
        logger.error("发送说说失败: %s", exc, exc_info=True)
        return False, f"发送说说失败: {exc}"
