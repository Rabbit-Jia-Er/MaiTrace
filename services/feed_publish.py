"""发送说说服务。

把发文本/发图片/custom 模式（取私聊最新内容）这些流程合到一处。
"""

from __future__ import annotations

from typing import Optional, Tuple

from ..utils import get_logger
from .activity import _classify_activity
from .chat_material import ChatMaterialError
from .feed_image import ImageGenerationError, collect_images_for_feed
from .llm_runner import LLMRunner
from .persona import resolve_persona
from .prompts import build_send_prompt
from .qzone_api import PublicationState, PublicationUncertainError
from .qzone_activity import record_activity
from .qzone_publisher import create_qzone_publisher

logger = get_logger(__name__)


async def _resolve_custom_content(plugin, bot_uin: str) -> Optional[str]:
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
    if not isinstance(stream, dict):
        logger.error("ctx.chat 返回非 dict: %s", type(stream).__name__)
        return None
    chat_id = stream.get("session_id")
    if not chat_id:
        logger.error("无法解析真实 session_id：%s", stream)
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

    messages = result
    if not isinstance(messages, list) or not messages:
        logger.error("custom 模式未获取到私聊消息")
        return None

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
    activity_type: str = "",
    outfit: str = "",
    publication: Optional[PublicationState] = None,
    material_instruction: str = "",
    interpret_custom: bool = True,
) -> Tuple[bool, str]:
    """主入口：根据 topic 生成内容并发到 QQ 空间。

    topic="custom" 时改走 _resolve_custom_content。
    Returns:
        (success, story) — 失败时 story 为错误消息。
    """
    # 同一次发布把日程场景直接传给绘卷，绘卷无需回调查询日程。
    activity_context = {}
    if plugin.schedule_engine is not None:
        snapshot = plugin.schedule_engine.current_activity_payload()
        activity = snapshot.get("activity")
        if isinstance(activity, dict):
            parameters = activity.get("parameters", {})
            if not current_activity:
                current_activity = str(activity.get("description") or activity.get("name") or "")
            if not activity_type:
                activity_type = _classify_activity(str(activity.get("goal_type") or ""), current_activity).value
            if not outfit:
                outfit = str(activity.get("outfit") or parameters.get("outfit", ""))
            activity_context = {
                "activity_location": str(activity.get("location") or parameters.get("location") or ""),
                "activity_time_window": str(activity.get("time_window") or ""),
                "activity_as_of": str(snapshot.get("as_of") or ""),
                "activity_timezone": str(snapshot.get("timezone") or ""),
            }
            if plugin.life_engine is not None:
                life = plugin.life_engine.peek_state()
                # 切换活动后旧场景不能套到新照片上；仅使用与这条快照一致的公开状态。
                if life.get("enabled") and life.get("current_activity") == (activity.get("description") or activity.get("name")):
                    activity_context["activity_scene"] = str(life.get("scene") or "")
                    if not activity_context["activity_location"]:
                        activity_context["activity_location"] = str(life.get("current_location") or "")
    if activity_type:
        activity_type = _classify_activity(activity_type, current_activity).value
    # 1–2. 只读探测实际客户端，再选择 SnowLuma 直发或 Cookie HTTP。
    try:
        qzone = await create_qzone_publisher(plugin)
    except Exception as exc:
        logger.error("连接 QQ 空间失败: %s", exc)
        return False, f"连接 QQ 空间失败: {exc}"
    if qzone is None:
        return False, "连接 QQ 空间失败，更新 cookies 失败或本地 Cookie 不存在"

    # 3. 决定说说内容
    if topic == "custom" and interpret_custom:
        if material_instruction:
            return False, "custom 直发模式不能消费带公开范围约束的生活素材"
        custom_message = await _resolve_custom_content(plugin, qzone.uin)
        if not custom_message:
            return False, "custom 模式取私聊内容失败"
        story = custom_message
        logger.info("custom 模式说说内容: %s", story)
        # custom 模式跳过 LLM 生成，但仍要加载 persona 给后续配图用
        persona = await resolve_persona(plugin)
    else:
        persona = await resolve_persona(plugin)
        try:
            prompt = await build_send_prompt(
                plugin, topic or "随机", persona.personality, persona.style,
                qzone_api=qzone, current_activity=current_activity,
                self_description=persona.self_description,
            )
        except ChatMaterialError as exc:
            logger.error("聊天素材整理失败，未发送: %s", exc)
            return False, str(exc)
        if material_instruction:
            # 指令由生活素材的固定隐私策略产生，不接受素材原文覆盖。
            prompt += "\n【本次素材的公开范围】\n" + material_instruction
        if plugin.config.llm.show_prompt:
            logger.info("发说说 prompt: %s", prompt)

        runner = LLMRunner(
            plugin.ctx,
            plugin.config.llm.text_model,
            timeout=plugin.config.llm.llm_timeout_seconds,
        )
        success, story = await runner.generate(prompt, temperature=0.3)
        if not success or not story:
            return False, f"生成说说内容失败: {story}"
        logger.info("生成说说内容: %s", story)

    # 4. 收集图片（reference_image_path 传给绘卷走 img2img；style 由 image.selfie_style 控制）
    try:
        images = await collect_images_for_feed(
            plugin, story,
            reference_image_path=persona.reference_image_path,
            activity_type=activity_type,
            activity_description=current_activity,
            outfit=outfit,
            activity_context=activity_context,
        )
    except ImageGenerationError as exc:
        # 配图约束仅在自产正文的链路生效；send_feed_api 始终纯直发。
        logger.error("配图生成失败，未发送说说: %s", exc)
        return False, f"配图生成失败，未发送: {exc}"

    # 5. 发布
    try:
        tid = await qzone.publish_emotion(story, images, publication=publication)
        if not tid:
            return False, "QQ 空间未返回发布成功的动态 ID"
        logger.info("成功发送说说, tid=%s", tid)
        await record_activity(plugin, "publish", fid=tid, text=story)
        return True, story
    except PublicationUncertainError as exc:
        logger.error("说说发布结果不明，不会自动重发: %s", exc)
        return False, str(exc)
    except Exception as exc:
        logger.error("发送说说失败: %s", exc, exc_info=True)
        return False, f"发送说说失败: {exc}"
