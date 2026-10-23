"""读说说服务：拉好友说说 + 评论 + 点赞 + 多层链式回复识别。"""

from __future__ import annotations

from typing import Any, Optional
import asyncio
import random

from ..utils import get_logger
from .adapter import resolve_bot_uin
from .cookie import renew_cookies
from .llm_runner import LLMRunner
from .persona import resolve_persona
from .prompts import build_comment_prompt
from .qzone_api import QzoneAPI, create_qzone_api
from .qzone_activity import record_activity

logger = get_logger(__name__)


def is_skip_comment(text: str) -> bool:
    """识别模型完整输出的跳过标记，不把含有这些词的普通评论误判为跳过。"""
    normalized = text.strip()
    # 识别引号、代码引号及双层括号包裹的跳过标记，保留其余正文。
    for _ in range(2):
        normalized = normalized.strip().strip("\"'“”‘’`")
        if len(normalized) >= 2 and normalized[0] in "[(（【" and normalized[-1] in "])）】":
            normalized = normalized[1:-1].strip()
    return "".join(normalized.split()).lower() in {
        "", "不回复", "不评论", "跳过", "无", "无评论", "无需回复", "不用回复", "不必回复",
        "skip", "none", "n/a", "na", "null",
    }


async def renew_cookies_from_plugin(plugin, *, uin: Optional[str] = None) -> bool:
    """便捷封装：从 plugin 取所有参数刷 cookie。"""
    if uin is None:
        uin = await resolve_bot_uin(plugin.ctx, list(plugin.config.plugin.adapter_priority))
    if not uin:
        logger.error("未找到真实 QQ 账号，无法刷新空间 Cookie")
        return False
    return await renew_cookies(
        plugin.ctx,
        storage=plugin.persistence,
        host=plugin.config.plugin.http_host,
        port=plugin.config.plugin.http_port,
        http_token=plugin.config.plugin.http_token,
        uin=uin,
        methods=list(plugin.config.plugin.cookie_methods),
        adapter_priority=list(plugin.config.plugin.adapter_priority),
    )


async def make_qzone_api(plugin) -> Optional[QzoneAPI]:
    """便捷封装：刷 cookie + 取 uin + 构造 QzoneAPI。"""
    uin = await resolve_bot_uin(plugin.ctx, list(plugin.config.plugin.adapter_priority))
    if not await renew_cookies_from_plugin(plugin, uin=uin):
        return None
    return await asyncio.to_thread(create_qzone_api, uin, storage=plugin.persistence)


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
        ok = await qzone.comment(fid, target_qq, content)
        if ok:
            await record_activity(plugin, "comment", target_qq=target_qq, fid=fid, text=content)
        return ok
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
        ok = await qzone.reply(fid, target_qq, target_nickname, content, comment_tid, host_uin=host_uin)
        if ok:
            await record_activity(plugin, "reply", target_qq=target_qq, target_name=target_nickname,
                                  fid=fid, comment_tid=comment_tid, text=content)
        return ok
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
    if not isinstance(result, list) or not result:
        return "未知用户", "无印象"
    row = result[0]
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
    enable_comment: bool = True,
) -> tuple[bool, list[dict[str, Any]] | str]:
    """读说说 + 按概率点赞评论。会修改 processed_list（去重缓存）。

    enable_comment=False 时不生成或发送评论，点赞与去重仍按配置执行。

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

    # 人格（含 multiple_reply_style 抽样 + self_description）
    persona = await resolve_persona(plugin)

    runner = LLMRunner(
        plugin.ctx,
        plugin.config.llm.text_model,
        timeout=plugin.config.llm.llm_timeout_seconds,
    )
    like_p = plugin.config.read.like_probability
    comment_p = plugin.config.read.comment_probability
    allow_skip = plugin.config.read.allow_skip_comment
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

        if enable_comment and random.random() <= comment_p:
            prompt = build_comment_prompt(
                plugin, target_name, content, created_time,
                persona.personality, persona.style, impression, rt_con,
                self_description=persona.self_description,
            )
            if show_prompt:
                logger.info("评论 prompt: %s", prompt)
            success, comment_message = await runner.generate(prompt, temperature=0.3)
            if success and allow_skip and is_skip_comment(comment_message):
                # 只略过评论，后面的点赞和去重标记仍须执行。
                logger.info("模型选择不评论说说 %s: %r", fid, comment_message)
                await record_activity(plugin, "skip_comment", target_qq=target_qq, target_name=target_name,
                                      fid=fid, feed=content)
            elif success and comment_message:
                ok = await qzone.comment(fid, target_qq, comment_message)
                if ok:
                    logger.info("评论成功: %s", comment_message)
                    await record_activity(plugin, "comment", target_qq=target_qq, target_name=target_name,
                                          fid=fid, feed=content, text=comment_message)
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
