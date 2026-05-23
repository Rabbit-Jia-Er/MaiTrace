"""/zn 命令子分发。

由 plugin.py 的 @Command("zn") 调用 dispatch_zn(self, **kwargs)。
日记相关子命令延迟 import services/diary，Step 5 完成后即可用。
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from ..services.feed_publish import send_feed
from ..services.permission import check_permission
from ..utils.date import parse_date, today_str

logger = logging.getLogger(__name__)


_KEYWORDS = {"gen", "generate", "ls", "list", "v", "view", "custom", "help"}

_HELP_TEXT = (
    "/zn <主题>          - 发一条指定主题的说说\n"
    "/zn custom          - 用 custom QQ 的私聊内容发说说\n"
    "/zn gen [日期]      - 生成日记（默认今天）\n"
    "/zn ls              - 日记列表\n"
    "/zn v [日期] [编号] - 查看日记\n"
    "/zn <日期>          - 等价 /zn v <日期>\n"
    "日期支持: YYYY-MM-DD / YYYY/MM/DD / YYYY.MM.DD / 今天 / 昨天 / 前天"
)


async def dispatch_zn(plugin, **kwargs: Any) -> tuple:
    """单一入口，按子命令分发。返回 (success, msg, intercept)。"""
    raw = ((kwargs.get("matched_groups") or {}).get("sub") or "").strip()
    stream_id = str(kwargs.get("stream_id", "") or "")
    user_id = str(kwargs.get("user_id", "") or "")
    group_id = str(kwargs.get("group_id", "") or "")

    if not raw or raw == "help":
        return await _send_help(plugin, stream_id)

    parts = raw.split(None, 1)
    cmd = parts[0].lower()
    param = parts[1].strip() if len(parts) > 1 else ""

    # ---- 日记子命令 ----
    if cmd in ("gen", "generate"):
        if not check_permission(plugin.config, user_id, "send"):
            await plugin.ctx.send.text(f"{user_id} 权限不足", stream_id)
            return False, "no perm", True
        return await _cmd_diary_gen(plugin, param, stream_id, group_id)

    if cmd in ("ls", "list"):
        if not check_permission(plugin.config, user_id, "send"):
            await plugin.ctx.send.text(f"{user_id} 权限不足", stream_id)
            return False, "no perm", True
        return await _cmd_diary_list(plugin, stream_id)

    if cmd in ("v", "view"):
        return await _cmd_diary_view(plugin, param, stream_id)

    # ---- custom 模式 ----
    if cmd == "custom":
        if not check_permission(plugin.config, user_id, "send"):
            await plugin.ctx.send.text(f"{user_id} 权限不足", stream_id)
            return False, "no perm", True
        return await _cmd_send_custom(plugin, stream_id)

    # ---- /zn <日期> 等价 /zn v <日期> ----
    date = parse_date(cmd)
    if date:
        view_param = f"{date} {param}".strip() if param else date
        return await _cmd_diary_view(plugin, view_param, stream_id)

    # ---- /zn <主题> 发说说 ----
    if not check_permission(plugin.config, user_id, "send"):
        await plugin.ctx.send.text(f"{user_id} 权限不足", stream_id)
        return False, "no perm", True
    return await _cmd_send_with_topic(plugin, raw, stream_id)


async def _send_help(plugin, stream_id: str) -> tuple:
    await plugin.ctx.send.text(_HELP_TEXT, stream_id)
    return True, "ok", True


# ----- 发说说 -----


async def _cmd_send_with_topic(plugin, topic: str, stream_id: str) -> tuple:
    success, story = await send_feed(plugin, topic)
    if not success:
        await plugin.ctx.send.text(f"发说说失败: {story}", stream_id)
        return False, story, True
    await plugin.ctx.send.text(f"已发送说说：\n{story}", stream_id)
    return True, "ok", True


async def _cmd_send_custom(plugin, stream_id: str) -> tuple:
    success, story = await send_feed(plugin, "custom")
    if not success:
        await plugin.ctx.send.text(f"发自定义说说失败: {story}", stream_id)
        return False, story, True
    await plugin.ctx.send.text(f"已发送说说：\n{story}", stream_id)
    return True, "ok", True


# ----- 日记（延迟 import services/diary） -----


def _try_import_diary():
    """延迟 import diary 子包，Step 5 完成前返回 None。"""
    try:
        from ..services import diary  # noqa: F401
        from ..services.diary import pipeline as diary_pipeline  # noqa: F401
        from ..services.diary.storage import DiaryStorage
        return diary_pipeline, DiaryStorage
    except ImportError as exc:
        logger.warning("日记模块尚未就绪: %s", exc)
        return None


async def _cmd_diary_gen(plugin, param: str, stream_id: str, group_id: str) -> tuple:
    mods = _try_import_diary()
    if mods is None:
        await plugin.ctx.send.text("日记功能未就绪（Step 5 待完成）", stream_id)
        return False, "diary not ready", True
    diary_pipeline, DiaryStorage = mods

    date = parse_date(param) if param else today_str()
    if not date:
        await plugin.ctx.send.text(f"日期格式错误: {param}", stream_id)
        return False, "bad date", True

    await plugin.ctx.send.text(f"正在生成 {date} 的日记...", stream_id)
    pipeline = diary_pipeline.DiaryPipeline(plugin)
    success, result = await pipeline.generate_for_date(date, group_id=group_id, ignore_filter=True)
    if not success:
        await plugin.ctx.send.text(f"生成失败: {result}", stream_id)
        return False, result, True

    publish_msg = ""
    try:
        ok = await pipeline.publish_to_qzone(date, result)
        publish_msg = " (已发布到空间)" if ok else " (空间发布失败)"
    except Exception as exc:
        publish_msg = f" (空间发布异常: {exc})"

    await plugin.ctx.send.text(f"{date} 日记已生成{publish_msg}:\n\n{result}", stream_id)
    return True, "ok", True


async def _cmd_diary_list(plugin, stream_id: str) -> tuple:
    mods = _try_import_diary()
    if mods is None:
        await plugin.ctx.send.text("日记功能未就绪（Step 5 待完成）", stream_id)
        return False, "diary not ready", True
    _, DiaryStorage = mods
    storage = DiaryStorage()
    stats = await storage.get_stats()
    diaries = await storage.list_diaries(limit=10)

    lines = [f"共{stats['total_count']}篇 | 均{stats['avg_words']}字 | 最新{stats['latest_date']}", ""]
    if diaries:
        for i, d in enumerate(diaries, 1):
            pub = "已发" if d.get("is_published_qzone") else "未发"
            lines.append(f"  {i}. {d.get('date', '?')} | {d.get('word_count', 0)}字 | {pub}")
    else:
        lines.append("暂无日记")
    await plugin.ctx.send.text("\n".join(lines), stream_id)
    return True, "ok", True


async def _cmd_diary_view(plugin, param: str, stream_id: str) -> tuple:
    mods = _try_import_diary()
    if mods is None:
        await plugin.ctx.send.text("日记功能未就绪（Step 5 待完成）", stream_id)
        return False, "diary not ready", True
    _, DiaryStorage = mods
    storage = DiaryStorage()

    params = param.split() if param else []
    if params:
        date = parse_date(params[0])
        if not date:
            await plugin.ctx.send.text(f"日期格式错误: {params[0]}", stream_id)
            return False, "bad date", True
    else:
        date = today_str()

    diaries = await storage.get_diaries_by_date(date)
    if not diaries:
        await plugin.ctx.send.text(f"{date} 没有日记", stream_id)
        return False, "no diary", True

    if len(params) > 1:
        try:
            idx = int(params[1]) - 1
            if 0 <= idx < len(diaries):
                d = diaries[idx]
                await plugin.ctx.send.text(
                    f"{date} #{idx + 1} ({d.get('word_count', 0)}字):\n\n{d.get('diary_content', '')}",
                    stream_id,
                )
                return True, "ok", True
            await plugin.ctx.send.text(f"编号超出范围，共{len(diaries)}条", stream_id)
            return False, "out of range", True
        except ValueError:
            pass

    d = diaries[-1]
    text = f"{date} 的日记 ({d.get('word_count', 0)}字)"
    if len(diaries) > 1:
        text += f"\n(共{len(diaries)}条，/zn v {date} <编号> 看其他)"
    text += f"\n\n{d.get('diary_content', '')}"
    await plugin.ctx.send.text(text, stream_id)
    return True, "ok", True
