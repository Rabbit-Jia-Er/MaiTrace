"""每日作息生成、查询和临时约定的工具业务实现。

通过 ``self._plugin.config`` 强类型访问插件配置，通过 ``self._plugin.ctx``
访问 SDK 能力代理；LLM 调用经共用的 ``LLMRunner`` 使用主程序 model_config。
"""

from datetime import datetime
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Tuple
import logging
import re

from .commitments import DEFAULT_DURATION_MINUTES, CommitmentConflictError, infer_requested_date, validate_commitment_date, validate_duration
from .planner.goal_manager import Goal, GoalStatus
from .planner.role_judge import judge_schedule_request
from .planner.schedule_generator import Schedule, ScheduleGenerator
from .privacy import goal_for_stream
from .utils.timezone_manager import TimezoneManager

if TYPE_CHECKING:
    from . import ScheduleEngine

logger = logging.getLogger(__name__)


class ToolsService:
    """承载每日作息查询、生成和临时约定。"""

    def __init__(self, engine: "ScheduleEngine") -> None:
        """初始化 ToolsService。

        Args:
            engine: 当前插件拥有的日程引擎，提供配置和数据库执行线程。
        """
        self._engine = engine
        self._plugin = engine.plugin
        logger.debug("日程工具服务已初始化")

    # ------------------------------------------------------------
    # 内部辅助
    # ------------------------------------------------------------

    def _build_schedule_config(self) -> Dict[str, Any]:
        """从插件强类型配置构建供 ScheduleGenerator 使用的 dict 配置。

        实际构造逻辑统一由 :meth:`ScheduleEngine.build_generator_config` 提供；
        本方法保留以兼容现有调用点（``update_schedule`` 等）。
        新代码请直接调用 ``self._engine.build_generator_config()``。
        """
        return self._engine.build_generator_config()

    def _make_tz_manager(self) -> TimezoneManager:
        """根据配置创建 TimezoneManager。"""
        return TimezoneManager(self._plugin.config.schedule.timezone)

    # ------------------------------------------------------------
    # 对外暴露：供 CommandService 直接复用的重生成入口
    # ------------------------------------------------------------

    async def regenerate_today_schedule_now(self, *, extra_prompt: str = "") -> Schedule:
        """立即重新生成今日日程（绕过 LLM 工具链）。

        供 ``CommandService`` 的 ``/zn plan regenerate`` 命令调用，封装了
        "构建配置 → 实例化 ScheduleGenerator → 调用 regenerate_today_schedule"
        的全流程，避免 command 层直接访问 ToolsService 的私有 ``_build_schedule_config``。

        Args:
            extra_prompt: 临时追加到 ``custom_prompt`` 的额外要求（可选），
                例如 "加入下午跑步"、"今天是生日"。生成完成后会自动还原。

        Returns:
            新生成的 :class:`Schedule` 对象（``auto_apply=True``，已写入数据库）。
        """
        await self._engine.prepare_generation()
        goal_manager = self._engine.goal_manager
        schedule_config = self._build_schedule_config()
        schedule_generator = ScheduleGenerator(
            goal_manager, config=schedule_config, plugin=self._plugin, run_db=self._engine.run_db,
        )
        return await schedule_generator.regenerate_today_schedule(
            user_id="system",
            chat_id="global",
            extra_prompt=extra_prompt,
            auto_apply=True,
        )


    # ------------------------------------------------------------
    # Tool 2：get_planning_status
    # ------------------------------------------------------------

    async def get_planning_status(self, **function_args: Any) -> Dict[str, Any]:
        """查询并返回今日日程（简洁格式）。"""
        try:
            goal_manager = self._engine.goal_manager
            detailed = bool(function_args.get("detailed", False))
            stream_id = str(function_args.get("stream_id") or function_args.get("chat_id") or "").strip()

            enable_detailed_description = self._plugin.config.schedule.enable_detailed_description

            # 统一获取今日日程目标（含日期过滤）
            schedule_goals = await self._engine.run_db(goal_manager.get_schedule_goals, chat_id="global")

            if not schedule_goals:
                if self._plugin.config.schedule.auto_generate:
                    generated = await self.generate_schedule(schedule_type="daily", auto_apply=True)
                    if generated["type"] == "error":
                        return generated
                    schedule_goals = await self._engine.run_db(goal_manager.get_schedule_goals, chat_id="global")
                if not schedule_goals:
                    return {"type": "planning_status", "content": "📅 今天还没有日程", "data": []}

            tz_manager = self._make_tz_manager()
            now = tz_manager.get_now()
            current_minutes = now.hour * 60 + now.minute

            # 正文和结构化返回使用同一展示副本，缺少会话时也隐藏私人约定。
            schedule_goals = [goal_for_stream(goal, stream_id) for goal in schedule_goals]

            # 提取时间窗口并排序
            schedule_with_time: List[Tuple[Goal, List[int]]] = []
            for goal in schedule_goals:
                time_window: Optional[List[int]] = None
                if goal.parameters and "time_window" in goal.parameters:
                    time_window = goal.parameters["time_window"]
                elif goal.conditions and "time_window" in goal.conditions:
                    time_window = goal.conditions["time_window"]

                if time_window and isinstance(time_window, list) and len(time_window) == 2:
                    schedule_with_time.append((goal, time_window))

            schedule_with_time.sort(key=lambda x: x[1][0])

            # 分类：正在进行 / 即将到来 / 已完成
            ongoing: List[Tuple[Goal, List[int]]] = []
            upcoming: List[Tuple[Goal, List[int]]] = []
            completed: List[Tuple[Goal, List[int]]] = []
            inactive: List[Tuple[Goal, List[int]]] = []
            for goal, tw in schedule_with_time:
                start_min, end_min = tw
                if goal.status == GoalStatus.COMPLETED:
                    completed.append((goal, tw))
                elif goal.status != GoalStatus.ACTIVE:
                    inactive.append((goal, tw))
                elif start_min <= current_minutes < end_min:
                    ongoing.append((goal, tw))
                elif current_minutes < start_min:
                    upcoming.append((goal, tw))
                else:
                    completed.append((goal, tw))

            def _fmt_time(minutes: int) -> str:
                h, m = minutes // 60, minutes % 60
                return f"{h:02d}:{m:02d}"

            def _fmt_item(goal: Any, tw: List[int], emoji: str = "") -> str:
                start_time = _fmt_time(tw[0])
                end_time = _fmt_time(tw[1])
                desc = ""
                if enable_detailed_description:
                    desc = goal.description
                    if len(desc) > 30:
                        desc = desc[:27] + "..."
                if desc:
                    return f"{emoji}{start_time}-{end_time} {goal.name}\n   💭 {desc}"
                return f"{emoji}{start_time}-{end_time} {goal.name}"

            date_str = now.strftime("%Y-%m-%d")
            weekday_names = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
            weekday = weekday_names[now.weekday()]

            content = f"📅 今日日程 {date_str} {weekday}\n"

            if ongoing:
                content += "\n🔵 正在进行:\n"
                for goal, tw in ongoing:
                    content += _fmt_item(goal, tw, "▶️ ") + "\n"

            if upcoming:
                content += "\n⏰ 即将到来:\n"
                for goal, tw in upcoming[:5]:
                    content += _fmt_item(goal, tw) + "\n"
                if len(upcoming) > 5:
                    content += f"   ...还有 {len(upcoming) - 5} 个活动\n"

            if completed and detailed:
                content += "\n✅ 已完成:\n"
                for goal, tw in completed[-3:]:
                    content += _fmt_item(goal, tw) + "\n"
            if inactive and detailed:
                labels = {GoalStatus.PAUSED: "已暂停", GoalStatus.CANCELLED: "已取消", GoalStatus.FAILED: "失败"}
                content += "\n⏸️ 暂停或取消:\n"
                for goal, tw in inactive:
                    content += _fmt_item(goal, tw) + f"（{labels[goal.status]}）\n"

            content += f"\n📊 共 {len(schedule_goals)} 个活动"
            if not detailed:
                content += " | 详情: detailed=true"

            return {"type": "planning_status", "content": content, "data": [goal.to_dict() for goal in schedule_goals]}

        except Exception as exc:
            logger.error(f"获取规划状态失败: {exc}", exc_info=True)
            return {"type": "error", "content": f"获取状态失败: {exc}"}

    # ------------------------------------------------------------
    # Tool 2：generate_schedule
    # ------------------------------------------------------------

    async def generate_schedule(self, **function_args: Any) -> Dict[str, Any]:
        """生成并应用日程。"""
        try:
            # 在请求模型前拒绝退役的周/月计划，旧记录继续保存在目标数据库中。
            if function_args.get("schedule_type", "daily") != "daily":
                return {"type": "error", "content": "仅保留每日生活作息，不再生成周/月计划"}
            await self._engine.prepare_generation()
            auto_apply = bool(function_args.get("auto_apply", True))
            chat_id = "global"
            user_id = "system"
            schedule_generator = ScheduleGenerator(
                self._engine.goal_manager, config=self._build_schedule_config(),
                plugin=self._plugin, run_db=self._engine.run_db,
            )
            schedule = await schedule_generator.generate_daily_schedule(
                user_id=user_id, chat_id=chat_id, use_llm=True,
            )

            summary = schedule_generator.get_schedule_summary(schedule)

            if auto_apply:
                # 若日程已存在，跳过应用
                if schedule.metadata and schedule.metadata.get("existing"):
                    return {
                        "type": "schedule_generated",
                        "content": f"✅ 今天的日程已经安排好了，一共 {len(schedule.items)} 个活动",
                    }
                created_ids = await schedule_generator.apply_schedule(
                    schedule=schedule,
                    user_id=user_id,
                    chat_id=chat_id,
                )
                return {
                    "type": "schedule_generated",
                    "content": f"✅ 日程生成完成！今天一共安排了 {len(created_ids)} 个活动",
                }

            return {"type": "schedule_generated", "content": summary, "data": schedule.to_dict()}

        except Exception as exc:
            logger.error(f"生成日程失败: {exc}", exc_info=True)
            return {"type": "error", "content": f"生成日程失败: {exc}"}

    # ------------------------------------------------------------
    # Tool 3：update_schedule（角色裁判式更新）
    # ------------------------------------------------------------

    async def update_schedule(self, **function_args: Any) -> Dict[str, Any]:
        """角色裁判决定是否接受；当日只插入空档，未来预约不限制天数。"""
        try:
            await self._engine.prepare_generation()
            description = str(function_args.get("description", "") or "").strip()
            if not description:
                return {"type": "error", "content": "请提供日程变更描述"}

            cfg = self._plugin.config.schedule
            goal_manager = self._engine.goal_manager
            target_stream_id = str(function_args.get("stream_id") or function_args.get("chat_id") or "").strip()
            creator_id = str(function_args.get("user_id") or "system")
            tz_manager = self._make_tz_manager()
            today = tz_manager.get_now()
            today_str = today.strftime("%Y-%m-%d")
            weekday = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"][today.weekday()]
            requested_date = infer_requested_date(description, today.date())
            if requested_date is not None:
                validate_commitment_date(requested_date, today.date())

            # 准备当前活动摘要 + 人设
            current_activities = []
            for goal in await self._engine.run_db(goal_manager.get_schedule_goals, chat_id="global"):
                goal = goal_for_stream(goal, target_stream_id)
                tw = (goal.parameters or {}).get("time_window") or (goal.conditions or {}).get("time_window")
                time_label = ""
                if tw and len(tw) >= 2:
                    h, m = divmod(int(tw[0]), 60)
                    eh, em = divmod(int(tw[1]), 60)
                    time_label = f"{h:02d}:{m:02d}–{eh:02d}:{em:02d}"
                current_activities.append({"time": time_label, "name": goal.name, "description": goal.description})
            persona = self._engine.bot_profile.get("personality", "")

            log_dir = self._engine.data_dir / "llm_logs"
            if cfg.role_judge_enabled:
                parsed = await judge_schedule_request(
                    self._plugin,
                    description=description,
                    current_activities=current_activities,
                    persona=persona,
                    today_str=today_str,
                    weekday=weekday,
                    model=cfg.llm_task_name,
                    temperature=cfg.role_judge_temperature,
                    max_tokens=cfg.max_tokens,
                    log_dir=log_dir,
                    log_enabled=cfg.llm_log_enabled,
                    current_time=today.strftime("%H:%M"),
                )
            else:
                # 裁判关闭仍执行日期和冲突约束，不把明天的请求写到今天。
                time_match = re.search(r"(?<!\d)([01]?\d|2[0-3]):([0-5]\d)(?!\d)", description)
                target = requested_date or today.date()
                parsed = {"decision": "future" if target > today.date() else "today",
                    "date": target.isoformat(), "time": f"{int(time_match[1]):02d}:{time_match[2]}" if time_match else "",
                    "title": description[:40], "notes": description, "reason": "",
                    "duration_minutes": DEFAULT_DURATION_MINUTES}

            if parsed is None:
                return {"type": "error", "content": "角色裁判未返回有效结果，日程未修改"}

            decision = parsed["decision"]
            reason = str(parsed.get("reason") or "").strip()

            if decision == "reject":
                msg = "😶‍🌫️ 我没接受这个请求，日程保持不变"
                if reason:
                    msg += f"\n理由：{reason}"
                return {"type": "schedule_unchanged", "content": msg}

            if decision == "future":
                target_date = str(parsed.get("date") or today_str)
                target = datetime.strptime(target_date, "%Y-%m-%d").date()
                validate_commitment_date(target, today.date())
                if target <= today.date():
                    return {
                        "type": "schedule_unchanged",
                        "content": f"⚠️ 未来约定日期 {target_date} 无效，日程不变",
                    }
                await self._engine.run_db(goal_manager.add_pending_commitment,
                    commitment_date=target_date,
                    title=str(parsed.get("title") or description[:40]),
                    time=str(parsed.get("time") or ""),
                    notes=str(parsed.get("notes") or ""),
                    reason=reason,
                    creator_id=creator_id,
                    target_stream_id=target_stream_id,
                    duration_minutes=validate_duration(parsed.get("duration_minutes", DEFAULT_DURATION_MINUTES)),
                )
                return {
                    "type": "schedule_committed",
                    "content": (
                        f"📅 已记录 {target_date} 的约定：{parsed.get('title')}\n"
                        f"{('备注：' + parsed.get('notes')) if parsed.get('notes') else ''}\n"
                        f"{('理由：' + reason) if reason else ''}"
                    ).strip(),
                }

            # 当日接受只新增一项，绝不调用整天重生成或删除原活动。
            if parsed.get("date") and str(parsed["date"]) != today_str:
                return {"type": "schedule_unchanged", "content": "约定日期与当日决定不符，原日程保持不变"}
            goal = await self._engine.run_db(goal_manager.insert_today_commitment,
                title=str(parsed.get("title") or description[:40]), time=str(parsed.get("time") or ""),
                notes=str(parsed.get("notes") or description), reason=reason, creator_id=creator_id,
                target_stream_id=target_stream_id,
                duration_minutes=validate_duration(parsed.get("duration_minutes", DEFAULT_DURATION_MINUTES)),
            )
            return {
                "type": "schedule_inserted",
                "content": (
                    f"✅ 已插入 {goal.parameters['commitment_time']} 的约定：{goal.name}，原有日程保留\n"
                    f"{('理由：' + reason) if reason else ''}"
                ).strip(),
                "data": goal.to_dict(),
            }

        except CommitmentConflictError as exc:
            return {"type": "schedule_unchanged", "content": str(exc), "available_times": exc.available_times}
        except Exception as exc:
            logger.error(f"update_schedule 失败: {exc}", exc_info=True)
            return {"type": "error", "content": f"更新日程失败: {exc}"}
