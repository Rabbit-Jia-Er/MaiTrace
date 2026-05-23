"""读说说服务：拉好友说说 + 评论 + 点赞 + 多层链式回复识别。"""

from __future__ import annotations

import asyncio
import logging
import random
from typing import Any, Optional

from ..utils import peel_envelope
from .cookie import renew_cookies
from .llm_runner import LLMRunner
from .prompts import build_comment_prompt
from .qzone_api import QzoneAPI, create_qzone_api

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


async def renew_cookies_from_plugin(plugin) -> bool:
    """便捷封装：从 plugin 取所有参数刷 cookie。"""
    uin = await _get_global_str(plugin.ctx, "bot.qq_account", "")
    return await renew_cookies(
        plugin.ctx,
        host=plugin.config.plugin.http_host,
        port=plugin.config.plugin.http_port,
        napcat_token=plugin.config.plugin.napcat_token,
        uin=uin,
        methods=list(plugin.config.plugin.cookie_methods),
    )


async def make_qzone_api(plugin) -> Optional[QzoneAPI]:
    """便捷封装：刷 cookie + 取 uin + 构造 QzoneAPI。"""
    if not await renew_cookies_from_plugin(plugin):
        return None
    uin = await _get_global_str(plugin.ctx, "bot.qq_account", "")
    return create_qzone_api(uin)


# ===== 单个动作 wrapper =====


async def read_feeds(plugin, target_qq: str, num: int) -> list[dict[str, Any]]:
    """获取指定 QQ 的说说列表，失败返回 []。"""
    qzone = await make_qzone_api(plugin)
    if qzone is None:
        return []
    try:
        return await qzone.get_list(target_qq, num)
    except Exception as exc:
        logger.error("get_list 异常: %s", exc, exc_info=True)
        return []


async def comment_feed(plugin, target_qq: str, fid: str, content: str) -> bool:
    """评论指定说说。"""
    qzone = await make_qzone_api(plugin)
    if qzone is None:
        return False
    try:
        return await qzone.comment(fid, target_qq, content)
    except Exception as exc:
        logger.error("comment 异常: %s", exc, exc_info=True)
        return False


async def like_feed(plugin, target_qq: str, fid: str) -> bool:
    """点赞指定说说。"""
    qzone = await make_qzone_api(plugin)
    if qzone is None:
        return False
    try:
        return await qzone.like(fid, target_qq)
    except Exception as exc:
        logger.error("like 异常: %s", exc, exc_info=True)
        return False


async def reply_feed(
    plugin,
    fid: str,
    target_qq: str,
    target_nickname: str,
    content: str,
    comment_tid: str,
    host_uin: Optional[str] = None,
) -> bool:
    """回复指定评论。"""
    qzone = await make_qzone_api(plugin)
    if qzone is None:
        return False
    try:
        return await qzone.reply(fid, target_qq, target_nickname, content, comment_tid, host_uin=host_uin)
    except Exception as exc:
        logger.error("reply 异常: %s", exc, exc_info=True)
        return False


# ===== 高级：读说说 + 点赞评论（ReadFeed Action 用）=====


async def _get_person_info(plugin, user_id: str) -> tuple[str, str]:
    """通过 ctx.db.get(PersonInfo) 查名字和印象。失败返回 ('未知用户', '无印象')。"""
    if not user_id:
        return "未知用户", "无印象"
    try:
        result = await plugin.ctx.db.get(
            model_name="PersonInfo",
            filters={"user_id": user_id},
        )
    except Exception as exc:
        logger.debug("db.get PersonInfo 异常: %s", exc)
        return "未知用户", "无印象"
    result = peel_envelope(result)
    rows = result if isinstance(result, list) else (result.get("rows") if isinstance(result, dict) else [])
    if not rows:
        return "未知用户", "无印象"
    row = rows[0]
    name = row.get("person_name") or "未知用户"
    impression = row.get("memory_points") or "无印象"
    return str(name), str(impression)


async def read_and_engage(
    plugin,
    target_qq: str,
    target_name: str,
    num: int,
    processed_list: dict[str, list[str]],
    *,
    cache_size: int = 100,
) -> tuple[bool, list[dict[str, Any]] | str]:
    """读说说 + 按概率点赞评论。会修改 processed_list（去重缓存）。

    Returns:
        (success, feeds_list_or_error_message)
    """
    feeds_list = await read_feeds(plugin, target_qq, num)
    if not feeds_list:
        return False, "未读取到说说"
    first = feeds_list[0]
    if isinstance(first, dict) and first.get("error"):
        return False, str(first.get("error"))

    qzone = await make_qzone_api(plugin)
    if qzone is None:
        return False, "cookie 不存在"

    from ..utils.date import format_date_str  # noqa: F401  (保留以便扩展)

    # 印象
    _, impression = await _get_person_info(plugin, target_qq)

    # 人格
    bot_personality = await _get_global_str(plugin.ctx, "personality.personality", "一个机器人")
    bot_expression = await _get_global_str(plugin.ctx, "personality.reply_style", "内容积极向上")

    runner = LLMRunner(
        plugin.ctx,
        plugin.config.llm.text_model,
        timeout=plugin.config.llm.llm_timeout_seconds,
    )
    like_p = plugin.config.read.like_probability
    comment_p = plugin.config.read.comment_probability
    show_prompt = plugin.config.llm.show_prompt

    for feed in feeds_list:
        if feed.get("error"):
            continue
        fid = feed["tid"]
        if fid in processed_list:
            continue
        await asyncio.sleep(3 + random.random())

        content = feed.get("content", "")
        for image in (feed.get("images") or []):
            content = content + image
        rt_con = feed.get("rt_con", "")
        created_time = feed.get("created_time", "")

        if random.random() <= comment_p:
            prompt = build_comment_prompt(
                plugin, target_name, content, created_time,
                bot_personality, bot_expression, impression, rt_con,
            )
            if show_prompt:
                logger.info("评论 prompt: %s", prompt)
            success, comment_message = await runner.generate(prompt, temperature=0.3)
            if success and comment_message:
                ok = await qzone.comment(fid, target_qq, comment_message)
                if ok:
                    logger.info("评论成功: %s", comment_message)
                else:
                    logger.warning("评论失败")
            else:
                logger.warning("生成评论失败: %s", comment_message)

        if random.random() <= like_p:
            ok = await qzone.like(fid, target_qq)
            if ok:
                logger.info("点赞成功: %s", content[:20])
            else:
                logger.warning("点赞失败")

        processed_list[fid] = []
        while len(processed_list) > cache_size:
            oldest = next(iter(processed_list))
            processed_list.pop(oldest)

    return True, feeds_list
