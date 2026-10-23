"""SendFeed / ReadFeed Tool 实现。

execute_send_feed / execute_read_feed 由 plugin.py 的 @Tool 委托调用。
"""

from __future__ import annotations

from typing import Any, Dict, List

from ..services.feed_publish import send_feed
from ..services.feed_read import read_and_engage
from ..services.permission import check_permission
from ..utils import get_logger, wrap_untrusted

logger = get_logger(__name__)


def _tool_failure(message: str) -> Dict[str, Any]:
    """让 Host 将业务失败识别为工具错误，并保留具体原因。"""
    return {"success": False, "content": message, "error": message}


def _clip_tool_text(value: Any, limit: int) -> str:
    text = str(value or "").strip()
    return text if len(text) <= limit else text[:limit] + "…（已截断）"


def _format_feed_content(target_name: str, feeds: List[Dict[str, Any]]) -> str:
    """返回有长度上限的动态正文，沿用外部数据围栏。"""
    sections = [f"好友：{_clip_tool_text(target_name, 100)}"]
    for index, feed in enumerate(feeds[:10], start=1):
        created_time = _clip_tool_text(feed.get("created_time"), 64) or "未知时间"
        content = _clip_tool_text(feed.get("content"), 2000) or "（无文字内容）"
        section = [f"[{index}] {created_time}", f"正文：{content}"]

        images = feed.get("images") or []
        if images:
            section.append("图片描述：" + "；".join(_clip_tool_text(item, 500) for item in images[:4]))
            if len(images) > 4:
                section.append(f"（另有 {len(images) - 4} 张图片未展示）")

        videos = feed.get("videos") or []
        if videos:
            section.append("视频：" + "；".join(_clip_tool_text(item, 300) for item in videos[:2]))

        forwarded = _clip_tool_text(feed.get("rt_con"), 1000)
        if forwarded:
            section.append(f"转发内容：{forwarded}")

        comments = feed.get("comments") or []
        if comments:
            section.append("评论：")
            for comment in comments[:5]:
                author = _clip_tool_text(comment.get("nickname") or comment.get("qq_account"), 100)
                text = _clip_tool_text(comment.get("content"), 300)
                section.append(f"- {author}：{text}")
            if len(comments) > 5:
                section.append(f"（另有 {len(comments) - 5} 条评论未展示）")

        sections.append("\n".join(section))
    if len(feeds) > 10:
        sections.append(f"（仅展示前 10 条，另有 {len(feeds) - 10} 条说说）")
    return f"已读取 {len(feeds)} 条说说。\n" + wrap_untrusted(
        "\n\n".join(sections), label="QQ 空间动态", max_chars=12000,
    )


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
    if not isinstance(result, list) or not result:
        return "", name
    user_id = str(result[0].get("user_id", "") or "")
    person_name = str(result[0].get("person_name", name) or name)
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


async def execute_send_feed(plugin, **kwargs: Any) -> Dict[str, Any]:
    """send_feed Tool 入口。

    @Tool 由 host 把 LLM function_args 平铺进 kwargs，
    同时附带 stream_id / chat_id / user_id / group_id / platform 上下文。
    """
    topic = (kwargs.get("topic") or "").strip()
    stream_id = kwargs.get("stream_id") or kwargs.get("chat_id") or ""

    # 权限只使用 Host 提供的调用者身份，不能用模型传入的名字替换身份。
    user_id = str(kwargs.get("user_id") or "").strip()
    if not check_permission(plugin.config, user_id, "send"):
        logger.info("%s 无 send_feed 权限", user_id)
        # 拒绝结果交回当前工具调用，由主程序决定回复及目标会话。
        return _tool_failure("当前调用者无权限发布说说。")

    if not topic:
        topic = "随机"
    logger.info("说说主题: %s", topic)

    success, story = await send_feed(plugin, topic)
    if not success:
        await _send_text(plugin, f"发说说失败: {story}", stream_id)
        return _tool_failure(str(story))

    # 成功结果交给模型处理，避免工具调用和模型最终回复各发送一次正文。
    return {
        "success": True,
        "content": "说说发送成功，正文如下：\n" + wrap_untrusted(
            story, label="已发布的说说正文", max_chars=12000,
        ),
    }


# ===== read_feed Tool =====


async def execute_recall_qzone_activity(plugin, **kwargs: Any) -> Dict[str, Any]:
    """空间历史与 read_feed 共用权限，结果只返回模型，不主动向聊天发消息。"""
    from ..services.qzone_activity import format_activity

    user_id = str(kwargs.get("user_id") or "")
    if not user_id or not check_permission(plugin.config, user_id, "read"):
        return _tool_failure("无权查询空间活动记录")
    try:
        limit = max(1, min(50, int(str(kwargs.get("limit") or "10"))))
    except ValueError:
        limit = 10
    keyword = str(kwargs.get("keyword") or "").strip()[:200]
    try:
        entries = await plugin.persistence.recent_qzone_activity(keyword=keyword, limit=limit)
    except Exception as exc:
        logger.warning("查询空间活动失败: %s", exc)
        return _tool_failure("空间活动记录读取失败，请稍后重试")
    return {"success": True, "content": format_activity(entries), "count": len(entries)}


def _parse_enable_comment(value: Any, default: bool = True) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"", "default", "默认"}:
        return default
    if text in {"0", "false", "no", "n", "off", "否", "不", "不回复", "不评论", "skip", "none"}:
        return False
    if text in {"1", "true", "yes", "y", "on", "是", "回复", "评论"}:
        return True
    return default


async def execute_read_feed(plugin, **kwargs: Any) -> Dict[str, Any]:
    """read_feed Tool 入口。"""
    target_name = (kwargs.get("target_name") or "").strip()
    stream_id = kwargs.get("stream_id") or kwargs.get("chat_id") or ""
    enable_comment = _parse_enable_comment(
        kwargs.get("enable_comment", kwargs.get("do_comment", kwargs.get("reply"))),
    )

    # 调用者权限只使用 Host 上下文，不能用模型传入的名字替换身份。
    user_id = str(kwargs.get("user_id") or "").strip()
    if not check_permission(plugin.config, user_id, "read"):
        # 不直接向工具上下文中的会话发送拒绝消息，避免越过主程序的回复路由。
        return _tool_failure("当前调用者无权限读取说说。")

    # 目标
    target_qq, target_name_resolved = await _resolve_person(plugin, target_name)
    if not target_qq:
        await _send_text(plugin, f"找不到{target_name}的 QQ", stream_id)
        return _tool_failure("未找到目标 user_id")

    num = plugin.config.read.read_number
    processed_list = await plugin.persistence.load_processed_list()
    cache_size = plugin.config.monitor.processed_feeds_cache_size
    success, result = await read_and_engage(
        plugin, target_qq, target_name_resolved, num,
        processed_list, cache_size=cache_size, enable_comment=enable_comment,
    )
    await plugin.persistence.save_processed_list(processed_list)

    if not success:
        await _send_text(plugin, f"读说说失败: {result}", stream_id)
        return _tool_failure(str(result))

    feeds = result if isinstance(result, list) else []
    return {
        "success": True,
        "content": _format_feed_content(target_name_resolved, feeds) + ("\n本次未发表评论。" if not enable_comment else ""),
        "count": len(feeds),
    }
