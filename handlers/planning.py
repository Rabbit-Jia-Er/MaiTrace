"""/zn plan 的日程管理入口，只管理每日生活作息与约定，日程图片复用主程序渲染。"""

from datetime import date, datetime, timedelta
from html import escape
from typing import TYPE_CHECKING, Any, List, Tuple
import logging

from ..services.schedule.utils.stream_filter import is_stream_allowed
from ..services.schedule.utils.time_utils import format_minutes_to_time, parse_time_window
from ..services.schedule.planner.goal_manager import GoalStatus
from ..services.schedule.privacy import goal_for_stream
from ..utils import get_global_str

if TYPE_CHECKING:
    from ..services.schedule import ScheduleEngine


logger = logging.getLogger(__name__)

_HELP = """日程规划命令：
/zn plan status：查看今天的生活作息
/zn plan list：查看日程图片（序号可用于删除）
/zn plan generate：生成并应用今日作息
/zn plan regenerate [额外要求]：生成成功后替换今日日程
/zn plan update <请求>：经角色裁判插入今天空档或预约未来日期，未来不限制天数，原活动保留
/zn plan delete <活动 ID 或图片序号>：删除活动
/zn plan clear [保留天数]：清理旧日程，保留今天和仍在持续的跨夜活动
/zn plan help：查看帮助"""

_STATUS_LABELS = {
    GoalStatus.ACTIVE: "进行中",
    GoalStatus.PAUSED: "已暂停",
    GoalStatus.COMPLETED: "已完成",
    GoalStatus.CANCELLED: "已取消",
    GoalStatus.FAILED: "失败",
}


class PlanningHandler:
    """命令管理器只持有当前 ScheduleEngine，不创建或查找全局管理器。"""

    def __init__(self, engine: "ScheduleEngine") -> None:
        self._engine = engine
        self._plugin = engine.plugin

    async def execute(
        self, content: str, *, stream_id: str = "", user_id: str = "",
        platform: str = "qq", group_id: str = "", **kwargs: Any,
    ) -> Tuple[bool, str, bool]:
        """处理完整命令或 plan 后的子命令；/zn 统一管理员校验由外层执行。"""
        del kwargs
        cfg = self._plugin.config.schedule
        if cfg.admin_users and user_id not in cfg.admin_users:
            return await self._finish(stream_id, False, "当前用户没有日程管理权限")
        stream_info = {"platform": platform, "group_id": group_id, "user_id": user_id}
        if not is_stream_allowed(stream_id, cfg.allowed_streams, stream_info):
            return await self._finish(stream_id, False, "当前聊天未启用日程功能")

        command = content.strip()
        for prefix in ("/zn plan", "/plan", "plan"):
            if command == prefix or command.startswith(prefix + " "):
                command = command[len(prefix):].strip()
                break
        action, _, param = command.partition(" ")
        action = action or "help"
        param = param.strip()
        try:
            if action == "help":
                return await self._finish(stream_id, True, _HELP)
            if action == "status":
                result = await self._engine.handle_tool(
                    "get_planning_status", detailed=True, stream_id=stream_id,
                    user_id=user_id, stream_info=stream_info,
                )
                return await self._finish(stream_id, result["type"] != "error", result["content"])
            if action == "list":
                return await self._show_image(stream_id)
            if action == "generate":
                result = await self._engine.handle_tool("generate_schedule", schedule_type=param or "daily")
                return await self._finish(stream_id, result["type"] != "error", result["content"])
            if action == "update":
                result = await self._engine.handle_tool(
                    "update_schedule", description=param, user_id=user_id,
                    stream_id=stream_id, stream_info=stream_info,
                )
                return await self._finish(stream_id, result["type"] != "error", result["content"])
            if action == "regenerate":
                async with self._engine.mutation_lock:
                    await self._engine.prepare_generation()
                    generator = self._engine.build_generator()
                    schedule = await generator.regenerate_today_schedule(
                        user_id=user_id, chat_id="global", extra_prompt=param, auto_apply=True,
                    )
                    await self._engine.refresh(force=True)
                return await self._finish(stream_id, True, f"今日日程已更新，共 {len(schedule.items)} 个活动")
            if action == "delete":
                return await self._delete(stream_id, param)
            if action == "clear":
                return await self._clear(stream_id, param)
            return await self._finish(stream_id, False, f"未知日程命令: {action}；使用 /zn plan help 查看帮助")
        except Exception as exc:
            logger.exception("日程命令执行失败: %s", action)
            return await self._finish(stream_id, False, f"日程操作失败: {exc}")

    async def _finish(self, stream_id: str, success: bool, message: str) -> Tuple[bool, str, bool]:
        if stream_id:
            await self._plugin.ctx.send.text(message, stream_id)
        return success, message, True

    async def _today_goals(self) -> List[Any]:
        goals = await self._engine.run_db(self._engine.goal_manager.get_schedule_goals, chat_id="global")
        return sorted(goals, key=lambda goal: self._window(goal)[0])

    @staticmethod
    def _window(goal: Any) -> Tuple[int, int]:
        window = goal.parameters.get("time_window") or goal.conditions.get("time_window")
        start, end = parse_time_window(window)
        if start is None or end is None:
            raise ValueError(f"目标 {goal.goal_id} 的时间窗口无效")
        return start, end

    async def _show_image(self, stream_id: str) -> Tuple[bool, str, bool]:
        goals = await self._today_goals()
        if not goals:
            return await self._finish(stream_id, True, "今天还没有日程，可使用 /zn plan generate 生成")
        if not stream_id:
            return False, "缺少发送日程图片的聊天流", True
        now = self._engine.timezone_manager.get_now()
        bot_name = (await get_global_str(self._plugin.ctx, "bot.nickname")).strip()
        rows = []
        for index, goal in enumerate(goals, 1):
            goal = goal_for_stream(goal, stream_id)
            start, end = self._window(goal)
            time_text = f"{format_minutes_to_time(start)}—{format_minutes_to_time(end)}"
            description = goal.description if self._plugin.config.schedule.enable_detailed_description else ""
            rows.append(
                f"<tr><td>{index}</td><td>{escape(time_text)}</td>"
                f"<td><strong>{escape(goal.name)}</strong><p>{escape(description)}</p></td>"
                f"<td>{_STATUS_LABELS[goal.status]}</td></tr>"
            )
        # BOT 名称、日程和描述仅作为文本插入，不允许模型或用户数据构成 HTML/脚本。
        html = (
            "<!doctype html><html lang='zh-CN'><meta charset='utf-8'>"
            "<style>body{margin:0;padding:28px;background:#f7f3ea;color:#283c36;"
            "font-family:'Microsoft YaHei',sans-serif}main{padding:24px;background:white;border-radius:18px}"
            "table{width:100%;border-collapse:collapse}td{padding:14px 10px;border-top:1px solid #e3e9e5}"
            "td:nth-child(2){white-space:nowrap}p{margin:6px 0;color:#5c6b63;font-size:14px;line-height:1.6}"
            "h1{font-size:24px;margin:0 0 8px}small{display:block;margin-bottom:24px;color:#66786c}</style>"
            f"<body><main><h1>{escape(bot_name)}的一天</h1><small>{now:%Y-%m-%d} · 共 {len(goals)} 个活动</small>"
            f"<table>{''.join(rows)}</table></main></body></html>"
        )
        result = await self._plugin.ctx.render.html2png(
            html, viewport={"width": 900, "height": 600}, full_page=True,
            allow_network=False, render_timeout_ms=20000,
        )
        if not isinstance(result, dict) or result.get("success") is False:
            raise RuntimeError(f"日程图片渲染失败: {result}")
        image_base64 = result.get("image_base64")
        if not isinstance(image_base64, str) or not image_base64:
            raise RuntimeError("日程图片渲染结果缺少 image_base64")
        sent = await self._plugin.ctx.send.image(image_base64, stream_id)
        if sent is False:
            raise RuntimeError("日程图片发送失败")
        return True, "已发送日程图片", True

    async def _delete(self, stream_id: str, identifier: str) -> Tuple[bool, str, bool]:
        if not identifier:
            return await self._finish(stream_id, False, "用法: /zn plan delete <目标 ID 或图片序号>")
        async with self._engine.mutation_lock:
            manager = self._engine.goal_manager
            if identifier.isdigit():
                goals = await self._today_goals()
                index = int(identifier) - 1
                if not 0 <= index < len(goals):
                    return await self._finish(stream_id, False, "日程序号超出范围")
                goal = goals[index]
            else:
                goal = await self._engine.run_db(manager.get_goal, identifier)
            if goal is None:
                return await self._finish(stream_id, False, "目标不存在")
            if not await self._engine.run_db(manager.delete_goal, goal.goal_id):
                raise RuntimeError("目标删除失败")
            await self._engine.refresh(force=True)
        return await self._finish(stream_id, True, f"已删除目标: {goal.name}")

    async def _clear(self, stream_id: str, param: str) -> Tuple[bool, str, bool]:
        if param and (not param.isdigit() or int(param) > 3650):
            return await self._finish(stream_id, False, "保留天数必须是 0 至 3650 的整数")
        keep_days = int(param) if param else 0
        now = self._engine.timezone_manager.get_now()
        cutoff = (now - timedelta(days=keep_days)).date()
        async with self._engine.mutation_lock:
            count = await self._engine.run_db(self._delete_old_schedule, cutoff, now)
            await self._engine.refresh(force=True)
        return await self._finish(stream_id, True, f"已清理 {count} 个旧日程")

    def _delete_old_schedule(self, cutoff: date, now: datetime) -> int:
        """在数据库线程内执行整批清理，保留当前仍持续的跨夜活动。"""
        manager = self._engine.goal_manager
        count = 0
        with manager.db._transaction():
            for goal in manager.get_all_goals(chat_id="global"):
                if goal.created_at is None:
                    continue
                created_at = goal.created_at
                created_at = created_at.replace(tzinfo=now.tzinfo) if created_at.tzinfo is None else created_at.astimezone(now.tzinfo)
                if created_at.date() >= cutoff:
                    continue
                if not (goal.parameters.get("time_window") or goal.conditions.get("time_window")):
                    continue
                _, end = self._window(goal)
                day = created_at.replace(hour=0, minute=0, second=0, microsecond=0)
                if day + timedelta(minutes=end) <= now and manager.delete_goal(goal.goal_id):
                    count += 1
        return count
