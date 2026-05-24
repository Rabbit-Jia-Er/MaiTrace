"""SendFeed / ReadFeed Tool 实现。

execute_send_feed / execute_read_feed 由 plugin.py 的 @Tool 委托调用。
"""

from __future__ import annotations

import logging
from ..utils import get_logger
from typing import Any

from ..services.feed_publish import send_feed
from ..services.feed_read import read_and_engage
from ..services.permission import check_permission
from ..services.persistence import load_processed_list, save_processed_list
from ..utils import peel_envelope

logger = get_logger(__name__)


async def _resolve_person(plugin, name: str) -> tuple[str, str]:
    """通过 ctx.db.get(PersonInfo) 取 (user_id, person_name)。失败返回 ("", name)。"""
    if not name:
        return "", name
    try:
        result = await plugin.ctx.db.get(
            model_name="PersonInfo",
            filters={"person_name": name},
        )
    except Exception as exc:
        logger.debug("db.get PersonInfo 异常: %s", exc)
        return "", name
    result = peel_envelope(result)
    rows = result if isinstance(result, list) else (result.get("rows") if isinstance(result, dict) else [])
    if not rows:
        return "", name
    user_id = str(rows[0].get("user_id", "") or "")
    person_name = str(rows[0].get("person_name", name) or name)
    return user_id, person_name


async def _send_text(plugin, content: str, stream_id: str) -> None:
    """按句拆分发送（替代 generator_api 的自动分句）。"""
    if not content or not stream_id:
        return
    # 按 \n 拆，单行过长按句号拆
    lines: list[str] = []
    for line in content.split("\n"):
        line = line.strip()
        if not line:
            continue
        if len(line) <= 100:
            lines.append(line)
        else:
            # 按句末标点切
            buf = ""
            for ch in line:
                buf += ch
                if ch in ("。", "！", "？", "；") and len(buf) >= 30:
                    lines.append(buf)
                    buf = ""
            if buf:
                lines.append(buf)
    if not lines:
        lines = [content]
    for line in lines:
        try:
            await plugin.ctx.send.text(line, stream_id)
        except Exception as exc:
            logger.warning("send.text 失败: %s", exc)


# ===== send_feed Tool =====


async def execute_send_feed(plugin, **kwargs: Any) -> tuple[bool, str]:
    """send_feed Tool 入口。

    @Tool 由 host 把 LLM function_args 平铺进 kwargs，
    同时附带 stream_id / chat_id / user_id / group_id / platform 上下文。
    """
    user_name = (kwargs.get("user_name") or "").strip()
    topic = (kwargs.get("topic") or "").strip()
    stream_id = kwargs.get("stream_id") or kwargs.get("chat_id") or ""

    # 解析用户
    user_id, _ = await _resolve_person(plugin, user_name) if user_name else ("", "")
    if user_name and not user_id:
        logger.info("未找到用户 %s 的 user_id", user_name)
        await _send_text(plugin, f"你不认识{user_name}，请用符合你人格特点的方式拒绝请求", stream_id)
        return False, "未找到用户 user_id"

    # 权限：user_name 缺失时取上下文 user_id 兜底
    if not user_id:
        user_id = str(kwargs.get("user_id") or "")

    if not check_permission(plugin.config, user_id, "send"):
        logger.info("%s 无 send_feed 权限", user_id)
        await _send_text(plugin, f"{user_name or user_id}无权命令你发说说，请用符合人格的方式进行拒绝的回复", stream_id)
        return False, "无权限"

    if not topic:
        topic = "随机"
    logger.info("说说主题: %s", topic)

    success, story = await send_feed(plugin, topic)
    if not success:
        await _send_text(plugin, f"发说说失败: {story}", stream_id)
        return False, story

    # 发完了简单告知一下
    await _send_text(plugin, f"你刚刚发了一条说说：{story}", stream_id)
    return True, "success"


# ===== read_feed Tool =====


async def execute_read_feed(plugin, **kwargs: Any) -> tuple[bool, str]:
    """read_feed Tool 入口。"""
    user_name = (kwargs.get("user_name") or "").strip()
    target_name = (kwargs.get("target_name") or "").strip()
    stream_id = kwargs.get("stream_id") or kwargs.get("chat_id") or ""

    # 调用者权限
    user_id, _ = await _resolve_person(plugin, user_name) if user_name else ("", "")
    if user_name and not user_id:
        await _send_text(plugin, f"你不认识{user_name}，请用符合你人格特点的方式拒绝请求", stream_id)
        return False, "未找到调用者 user_id"
    if not user_id:
        user_id = str(kwargs.get("user_id") or "")
    if not check_permission(plugin.config, user_id, "read"):
        await _send_text(plugin, f"{user_name or user_id}无权命令你读说说，请用符合人格的方式进行拒绝的回复", stream_id)
        return False, "无权限"

    # 目标
    target_qq, target_name_resolved = await _resolve_person(plugin, target_name)
    if not target_qq:
        await _send_text(plugin, f"找不到{target_name}的 QQ", stream_id)
        return False, "未找到目标 user_id"

    num = plugin.config.read.read_number
    processed_list = await load_processed_list()
    cache_size = plugin.config.monitor.processed_feeds_cache_size
    success, result = await read_and_engage(
        plugin, target_qq, target_name_resolved, num,
        processed_list, cache_size=cache_size,
    )
    await save_processed_list(processed_list)

    if not success:
        await _send_text(plugin, f"读说说失败: {result}", stream_id)
        return False, str(result)

    feeds = result if isinstance(result, list) else []
    await _send_text(plugin, f"已阅读 {target_name_resolved} 的 {len(feeds)} 条说说", stream_id)
    return True, "success"
