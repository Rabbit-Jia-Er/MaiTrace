"""/zn 命令子分发。

由 plugin.py 的 @Command("zn") 调用 dispatch_zn(self, **kwargs)。
日记相关子命令延迟 import services/diary，Step 5 完成后即可用。
"""

from __future__ import annotations

import datetime
import logging
from ..utils import get_logger
from typing import Any

from ..services.feed_publish import send_feed
from ..services.permission import is_admin
from ..utils.date import parse_date, today_str

logger = get_logger(__name__)


_KEYWORDS = {"gen", "generate", "ls", "list", "v", "view", "custom", "help", "debug"}

_HELP_TEXT = (
    "/zn <主题>          - 发一条指定主题的说说\n"
    "/zn custom          - 用 custom QQ 的私聊内容发说说\n"
    "/zn gen [日期]      - 生成日记（默认今天）\n"
    "/zn ls              - 日记列表\n"
    "/zn v [日期] [编号] - 查看日记\n"
    "/zn <日期>          - 等价 /zn v <日期>\n"
    "/zn debug [项]      - 调试信息 (routine/msgs/cookie/help)\n"
    "日期支持: YYYY-MM-DD / YYYY/MM/DD / YYYY.MM.DD / 今天 / 昨天 / 前天"
)


async def dispatch_zn(plugin, **kwargs: Any) -> tuple:
    """单一入口，按子命令分发。返回 (success, msg, intercept)。

    所有 /zn 子命令统一要求调用者在 ``[plugin].admin_qq`` 列表中。
    """
    raw = ((kwargs.get("matched_groups") or {}).get("sub") or "").strip()
    stream_id = str(kwargs.get("stream_id", "") or "")
    user_id = str(kwargs.get("user_id", "") or "")
    group_id = str(kwargs.get("group_id", "") or "")

    # ===== 全局管理员检查（覆盖所有子命令：help / 主题 / custom / gen / ls / v / debug / <日期>） =====
    if not is_admin(plugin.config, user_id):
        admin_list = list(plugin.config.plugin.admin_qq or [])
        msg = "⚠️ 未配置管理员" if not admin_list else "⚠️ 仅管理员可用"
        await plugin.ctx.send.text(msg, stream_id)
        return False, "no admin", True

    # ===== 子命令分发 =====
    if not raw or raw == "help":
        return await _send_help(plugin, stream_id)

    parts = raw.split(None, 1)
    cmd = parts[0].lower()
    param = parts[1].strip() if len(parts) > 1 else ""

    # ---- 日记子命令 ----
    if cmd in ("gen", "generate"):
        return await _cmd_diary_gen(plugin, param, stream_id, group_id)

    if cmd in ("ls", "list"):
        return await _cmd_diary_list(plugin, stream_id)

    if cmd in ("v", "view"):
        return await _cmd_diary_view(plugin, param, stream_id)

    # ---- 调试子命令 ----
    if cmd == "debug":
        return await _cmd_debug(plugin, param, stream_id)

    # ---- custom 模式 ----
    if cmd == "custom":
        return await _cmd_send_custom(plugin, stream_id)

    # ---- /zn <日期> 等价 /zn v <日期> ----
    date = parse_date(cmd)
    if date:
        view_param = f"{date} {param}".strip() if param else date
        return await _cmd_diary_view(plugin, view_param, stream_id)

    # ---- /zn <主题> 发说说 ----
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


# ----- /zn debug 子命令 -----


_DEBUG_HELP_TEXT = (
    "/zn debug routine        - 最近 Routine LLM 决策\n"
    "/zn debug msgs [日期]    - 当日消息读取统计\n"
    "/zn debug cookie         - Cookie 当前状态\n"
    "/zn debug help           - 本帮助"
)


async def _cmd_debug(plugin, param: str, stream_id: str) -> tuple:
    parts = param.split(None, 1) if param else []
    sub = parts[0].lower() if parts else "help"
    sub_param = parts[1].strip() if len(parts) > 1 else ""

    if sub == "help":
        await plugin.ctx.send.text(_DEBUG_HELP_TEXT, stream_id)
        return True, "ok", True
    if sub == "routine":
        return await _debug_routine(plugin, stream_id)
    if sub == "msgs":
        return await _debug_msgs(plugin, sub_param, stream_id)
    if sub == "cookie":
        return await _debug_cookie(plugin, stream_id)

    await plugin.ctx.send.text(f"未知调试项: {sub}\n\n{_DEBUG_HELP_TEXT}", stream_id)
    return False, "unknown debug sub", True


def _fmt_ts(ts: float) -> str:
    if not ts:
        return "-"
    return datetime.datetime.fromtimestamp(ts).strftime("%m-%d %H:%M:%S")


async def _debug_routine(plugin, stream_id: str) -> tuple:
    routine = getattr(plugin, "_routine", None)
    if routine is None:
        await plugin.ctx.send.text("Routine 未启动（plugin.enabled=false？）", stream_id)
        return True, "no routine", True
    history = routine.get_decision_history()

    lines: list[str] = []
    snapshot = routine.get_planning_snapshot() if hasattr(routine, "get_planning_snapshot") else None
    if snapshot is None:
        lines.append("规划插件 API：尚未收到任何响应（autonomous-planning-plugin-v4 未安装？）")
    elif not snapshot.get("has_activity"):
        lines.append(f"规划插件 API：has_activity=False ({snapshot.get('as_of', '')})")
    else:
        act = snapshot.get("activity") or {}
        lines.append(
            f"规划插件 API：[{act.get('goal_type', '')}] {act.get('name', '')} "
            f"({act.get('time_window') or '-'})"
        )

    if not history:
        lines.append("Routine 尚未产生决策记录（启动 < 1 个 check 周期）")
        await plugin.ctx.send.text("\n".join(lines), stream_id)
        return True, "empty history", True

    lines.append("")
    lines.append(f"最近 {len(history)} 次 Routine 决策（按时间倒序）：")
    for entry in reversed(history):
        ts = _fmt_ts(entry.get("ts", 0))
        action = entry.get("action", "?")
        atype = entry.get("activity_type", "?")
        adesc = (entry.get("activity_desc", "") or "")[:24]
        reason = (entry.get("reason", "") or "").strip()
        if entry.get("cooldown_skipped"):
            result = "冷却跳过"
        elif entry.get("hard_blocked"):
            result = f"硬规则拒绝 ({reason})" if reason else "硬规则拒绝"
        elif entry.get("dice_skipped"):
            result = f"LLM=是 但掷骰跳过 ({reason})" if reason else "LLM=是 但掷骰跳过"
        elif entry.get("decision"):
            executed = entry.get("executed")
            if executed is True:
                result = f"✓ 已执行 ({reason})" if reason else "✓ 已执行"
            elif executed is False:
                err = entry.get("error", "") or "?"
                result = f"✗ 执行失败 ({err})"
            else:
                result = f"决策=是，执行中 ({reason})" if reason else "决策=是，执行中"
        else:
            if reason:
                result = f"决策=否 ({reason})"
            else:
                answer = (entry.get("llm_answer", "") or "").strip().replace("\n", " ")[:30]
                result = f"决策=否 ({answer})" if answer else "决策=否"
        lines.append(f"  {ts} [{action}] {atype}:{adesc} → {result}")
    await plugin.ctx.send.text("\n".join(lines), stream_id)
    return True, "ok", True


async def _debug_msgs(plugin, param: str, stream_id: str) -> tuple:
    date = parse_date(param) if param else today_str()
    if not date:
        await plugin.ctx.send.text(f"日期格式错误: {param}", stream_id)
        return False, "bad date", True

    try:
        from ..services.diary.fetcher import MessageFetcher
    except Exception as exc:
        await plugin.ctx.send.text(f"加载 fetcher 失败: {exc}", stream_id)
        return False, "import fail", True

    date_obj = datetime.datetime.strptime(date, "%Y-%m-%d")
    start = date_obj.timestamp()
    now = datetime.datetime.now()
    end = now.timestamp() if now.strftime("%Y-%m-%d") == date else (
        date_obj + datetime.timedelta(days=1)
    ).timestamp()

    fetcher = MessageFetcher(plugin.ctx)
    messages = await fetcher.fetch_all(start, end)

    bot_qq = await _get_bot_qq(plugin)
    bot_msgs = user_msgs = 0
    by_chat: dict[str, int] = {}
    for msg in messages:
        info = (msg.get("message_info") or {}).get("user_info") or {}
        uid = str(info.get("user_id", "") or "")
        if bot_qq and uid == bot_qq:
            bot_msgs += 1
        else:
            user_msgs += 1
        sid = str(msg.get("session_id", "") or "")
        if sid:
            by_chat[sid] = by_chat.get(sid, 0) + 1

    lines = [
        f"📅 {date} 消息读取调试 (bot_qq={bot_qq or '未配置'})",
        f"  总消息: {len(messages)}    用户: {user_msgs}    bot: {bot_msgs}",
        f"  活跃聊天: {len(by_chat)} 个",
    ]
    if by_chat:
        top = sorted(by_chat.items(), key=lambda kv: kv[1], reverse=True)[:10]
        lines.append("  Top 10 聊天:")
        for sid, n in top:
            lines.append(f"    {sid[:32]}...  {n} 条")
    await plugin.ctx.send.text("\n".join(lines), stream_id)
    return True, "ok", True


async def _debug_cookie(plugin, stream_id: str) -> tuple:
    try:
        from ..services.cookie import get_cookie_state
        from ..services.persistence import load_cookie_stats
    except Exception as exc:
        await plugin.ctx.send.text(f"加载 cookie 模块失败: {exc}", stream_id)
        return False, "import fail", True

    state = get_cookie_state()
    stats = await load_cookie_stats()

    lines = ["🍪 Cookie 状态："]
    lines.append(f"  上次方法: {state.get('last_method') or '-'}")
    lines.append(f"  上次保存: {_fmt_ts(state.get('last_save_time', 0))}")
    lines.append(f"  绑定 uin: {state.get('uin') or '-'}")
    last_err = state.get("last_error") or ""
    if last_err:
        lines.append(f"  最近错误: {last_err[:120]}")

    if stats:
        lines.append("")
        lines.append("📊 各方式成功率（近期累计）：")
        for method, entry in stats.items():
            s = int(entry.get("success", 0) or 0)
            f = int(entry.get("failure", 0) or 0)
            total = s + f
            rate = (s / total * 100) if total > 0 else 0.0
            last = _fmt_ts(entry.get("last_success_ts", 0))
            lines.append(f"  {method:10s} {s}成功 / {f}失败 ({rate:.0f}%, 最近成功 {last})")
    else:
        lines.append("")
        lines.append("(尚无 cookie_stats.json，首次启动按用户原顺序尝试)")
    await plugin.ctx.send.text("\n".join(lines), stream_id)
    return True, "ok", True


async def _get_bot_qq(plugin) -> str:
    try:
        value = await plugin.ctx.config.get("bot.qq_account", "")
    except Exception:
        return ""
    from ..utils import peel_envelope
    value = peel_envelope(value)
    if isinstance(value, dict):
        value = value.get("value", "")
    return str(value or "")
