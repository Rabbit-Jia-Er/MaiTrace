"""Automatic Schedule Scheduler.

This module provides automatic scheduling functionality that generates
daily schedules at configured times.

The scheduler runs as a background task and automatically creates
new daily schedules based on configuration settings.

Example:
    >>> from auto_scheduler import ScheduleAutoScheduler
    >>> scheduler = ScheduleAutoScheduler(plugin)
    >>> await scheduler.start()  # Start background task
"""

from typing import TYPE_CHECKING, Any, Dict, Optional, Tuple
import asyncio
import datetime
import json
import logging

from ..utils.timezone_manager import TimezoneManager
from ..llm import generate_text
from .generator import BaseScheduleGenerator

if TYPE_CHECKING:
    from .. import ScheduleEngine

logger = logging.getLogger(__name__)


class ScheduleAutoScheduler:
    """
    日程自动调度器类

    负责管理日程的定时生成任务，在每天配置的时间自动生成新一天的日程。

    Attributes:
        plugin: 插件实例引用
        is_running (bool): 任务运行状态
        task: 异步任务对象
        logger: 日志记录器
        _retry_count (int): P1优化 - 连续失败计数
        _max_retry_wait (int): P1优化 - 最大重试等待时间（秒）

    Methods:
        start: 启动定时任务
        stop: 停止定时任务
        _schedule_loop: 定时任务循环
        _generate_today_schedule: 生成今日日程
    """

    def __init__(self, plugin: Any, *, engine: "ScheduleEngine") -> None:
        """
        初始化定时任务调度器

        Args:
            plugin: 插件实例，用于获取配置和执行日程生成
        """
        self.plugin = plugin
        self._engine = engine
        self.is_running = False
        self.task = None
        self.logger = logging.getLogger(__name__)

        # 初始化时区管理器
        timezone_str = plugin.config.schedule.timezone
        self.tz_manager = TimezoneManager(timezone_str)

        # P1优化：指数退避参数
        self._retry_count = 0
        self._max_retry_wait = 300  # 最大等待5分钟
        self._last_schedule_date: Optional[str] = None
        self._last_infer_date: Optional[str] = None
        self._inferred_prompt_cache: Dict[str, str] = {}
        self._inferred_prompt_file = engine.data_dir / "next_day_prompt.json"

        # 导入依赖（延迟导入避免循环依赖）
        from .schedule_generator import ScheduleGenerator

        self.ScheduleGenerator = ScheduleGenerator

    async def start(self):
        """
        启动定时任务

        检查插件配置，如果启用了定时生成功能，则启动定时任务。
        启动成功后会创建异步任务循环，等待配置的时间点执行日程生成。
        """
        if self.is_running:
            return

        # 定时生成和前夜推断共用循环，但开关独立。
        config = self.plugin.config.schedule
        if not (config.auto_schedule_enabled or config.auto_infer_next_day_prompt):
            self.logger.info("日程定时生成与次日策略推断均未启用")
            return

        await asyncio.to_thread(self._load_inferred_prompt_cache)
        self.is_running = True
        self.task = asyncio.create_task(self._schedule_loop())
        schedule_time = self.plugin.config.schedule.auto_schedule_time
        self.logger.info(f"日程定时服务已启动 - 生成开关: {config.auto_schedule_enabled}, 时间: {schedule_time}, 推断开关: {config.auto_infer_next_day_prompt}")

    async def stop(self):
        """
        停止定时任务

        取消正在运行的定时任务，并等待任务完全结束。
        确保资源正确释放，避免任务泄漏。
        """
        if not self.is_running:
            return

        self.is_running = False
        if self.task:
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass
        self.logger.info("日程定时任务已停止")

    @staticmethod
    def _normalize_time_str(time_str: str) -> str:
        """检查配置时间，格式无效时明确报错，不改用另一个执行时间。"""
        try:
            hour_str, minute_str = str(time_str).split(":", 1)
            hour = int(hour_str)
            minute = int(minute_str)
            if 0 <= hour <= 23 and 0 <= minute <= 59:
                return f"{hour:02d}:{minute:02d}"
        except (TypeError, ValueError) as exc:
            raise ValueError(f"日程调度时间格式无效: {time_str!r}，应为 HH:MM") from exc
        raise ValueError(f"日程调度时间超出范围: {time_str!r}")

    def _is_time_due(self, now: datetime.datetime, time_str: str, last_date: Optional[str]) -> bool:
        today_str = now.strftime("%Y-%m-%d")
        if last_date == today_str:
            return False
        normalized = self._normalize_time_str(time_str)
        hour_str, minute_str = normalized.split(":")
        target_time = now.replace(hour=int(hour_str), minute=int(minute_str), second=0, microsecond=0)
        return now >= target_time

    def _seconds_until_next(self, now: datetime.datetime, time_str: str, last_date: Optional[str]) -> float:
        normalized = self._normalize_time_str(time_str)
        hour_str, minute_str = normalized.split(":")
        target_time = now.replace(hour=int(hour_str), minute=int(minute_str), second=0, microsecond=0)
        today_str = now.strftime("%Y-%m-%d")

        if last_date != today_str and now < target_time:
            return max(1.0, (target_time - now).total_seconds())

        next_target = target_time + datetime.timedelta(days=1)
        return max(1.0, (next_target - now).total_seconds())

    @staticmethod
    def _extract_time_window(goal) -> Tuple[Optional[int], Optional[int]]:
        time_window = None
        if goal.parameters and "time_window" in goal.parameters:
            time_window = goal.parameters["time_window"]
        elif goal.conditions and "time_window" in goal.conditions:
            time_window = goal.conditions["time_window"]

        if isinstance(time_window, (list, tuple)) and len(time_window) == 2:
            try:
                return int(time_window[0]), int(time_window[1])
            except Exception:
                return None, None
        return None, None

    @staticmethod
    def _format_minutes(value: int) -> str:
        hours = value // 60
        minutes = value % 60
        return f"{hours:02d}:{minutes:02d}"

    def _load_inferred_prompt_cache(self) -> None:
        try:
            if not self._inferred_prompt_file.exists():
                return
            with open(self._inferred_prompt_file, "r", encoding="utf-8") as f:
                loaded = json.load(f)
            if isinstance(loaded, dict):
                self._inferred_prompt_cache = loaded
            else:
                raise ValueError("次日策略缓存必须是 JSON 对象")
        except Exception as e:
            self.logger.error(f"加载次日推断缓存失败: {e}")
            raise

    def _save_inferred_prompt_cache(self) -> None:
        try:
            self._inferred_prompt_file.parent.mkdir(parents=True, exist_ok=True)
            temporary = self._inferred_prompt_file.with_suffix(".json.tmp")
            with open(temporary, "w", encoding="utf-8") as f:
                json.dump(self._inferred_prompt_cache, f, ensure_ascii=False, indent=2)
            temporary.replace(self._inferred_prompt_file)
        except Exception as e:
            self.logger.error(f"保存次日推断缓存失败: {e}")
            raise

    def _build_recent_schedule_summary(self, goal_manager, lookback_days: int, include_completion: bool) -> str:
        now = self.tz_manager.get_now()
        sections = []

        for offset in range(lookback_days):
            day = now - datetime.timedelta(days=offset)
            date_str = day.strftime("%Y-%m-%d")
            goals = goal_manager.get_schedule_goals(chat_id="global", date_str=date_str)
            if not goals:
                continue

            def _sort_key(goal):
                start, _ = self._extract_time_window(goal)
                return start if start is not None else 10**9

            goals = sorted(goals, key=_sort_key)
            lines = [f"[{date_str}] 共{len(goals)}项"]

            for goal in goals:
                start, end = self._extract_time_window(goal)
                if start is not None and end is not None:
                    time_text = f"{self._format_minutes(start)}-{self._format_minutes(end)}"
                else:
                    time_text = "未知时段"

                priority = goal.priority.value
                goal_type = str(goal.goal_type or "custom")
                item = f"- {time_text} {goal.name} ({goal_type}/{priority})"
                description = " ".join(str(goal.description or "").split())
                if description:
                    item += f" 描述={description[:96]}{'…' if len(description) > 96 else ''}"

                if include_completion:
                    # Goal 上的 status 一定存在；progress 在 core.models 中类型为 int 默认 0
                    status = goal.status.value
                    progress = goal.progress
                    item += f" 状态={status}, 进度={progress}%"

                lines.append(item)

            sections.append("\n".join(lines))

        return "\n\n".join(sections)

    async def _infer_next_day_prompt(self, schedule_config: Dict[str, Any]) -> bool:
        lookback_days = int(schedule_config.get("infer_lookback_days", 3) or 3)
        lookback_days = max(1, min(7, lookback_days))

        max_chars = int(schedule_config.get("infer_max_prompt_chars", 300) or 300)
        max_chars = max(80, min(800, max_chars))

        include_completion = bool(schedule_config.get("infer_use_completion_signal", True))

        goal_manager = self._engine.goal_manager
        history_summary = await self._engine.run_db(
            self._build_recent_schedule_summary, goal_manager, lookback_days, include_completion
        )
        if not history_summary.strip():
            self.logger.info("次日推断跳过：近期无可用日程历史")
            return False

        base_prompt = str(schedule_config.get("custom_prompt", "") or "").strip()
        now = self.tz_manager.get_now()
        tomorrow = now + datetime.timedelta(days=1)
        target_date = tomorrow.strftime("%Y-%m-%d")
        weekday_names = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
        target_weekday = weekday_names[tomorrow.weekday()]

        instruction = (
            "你是日程策略设计助手。"
            "请基于最近几天的日程与完成状态，为指定目标日期生成一段中文'策略提示词'。"
            f"这段策略将在{target_date}当天使用，请以目标日的'今天'视角描述，"
            "不要用'明天再做'把计划推迟一天。"
            "输出必须是单段纯文本，不要Markdown、不要列表、不要解释。"
            f"长度控制在120到{max_chars}字之间，强调连续性与现实可执行性。"
            "基础固定要求代表持续方向，必须优先满足；自动策略只能在此基础上细化。"
            "承接近期地点、进行中的项目和未完成事件，不能无缘由结束或切换。"
        )

        prompt = (
            f"{instruction}\n\n"
            f"目标日期：{target_date} {target_weekday}\n"
            f"基础固定要求（可为空）：{base_prompt if base_prompt else '无'}\n\n"
            f"最近日程历史：\n{history_summary}\n\n"
            "请直接输出策略提示词正文："
        )

        try:
            model_helper = BaseScheduleGenerator(goal_manager, schedule_config)
            task_name, max_tokens, temperature = model_helper.get_model_config()
            infer_max_tokens = max(256, min(1024, int(max_tokens)))
            infer_temperature = max(0.2, min(0.8, float(temperature)))

            # v4：必须通过插件 ctx.llm 调用，未注入 plugin 视为编程错误
            if self.plugin is None:
                raise RuntimeError("ScheduleAutoScheduler 未注入 plugin 实例，无法调用 ctx.llm.generate")

            llm_result = await generate_text(
                self.plugin,
                prompt=prompt,
                model=task_name,
                max_tokens=infer_max_tokens,
                temperature=infer_temperature,
            )
            success = bool(llm_result.get("success", False))
            response = str(llm_result.get("response", ""))
            if not success or not response:
                raise RuntimeError(f"次日策略推断失败: {response or '模型返回空内容'}")

            inferred_prompt = str(response).strip().replace("```", "")
            inferred_prompt = " ".join(inferred_prompt.split())
            inferred_prompt = inferred_prompt.strip("\"' ")
            if len(inferred_prompt) > max_chars:
                inferred_prompt = inferred_prompt[:max_chars].rstrip()

            if len(inferred_prompt) < 20:
                raise ValueError("次日策略推断结果过短")

            self._inferred_prompt_cache = {
                "target_date": target_date,
                "prompt": inferred_prompt,
                "generated_at": now.isoformat(),
            }
            await asyncio.to_thread(self._save_inferred_prompt_cache)
            self.logger.info(f"✅ 次日策略推断成功，目标日期: {target_date}")
            return True
        except Exception as e:
            self.logger.error(f"次日策略推断异常: {e}", exc_info=True)
            raise

    def _get_effective_custom_prompt(self, today: str, fallback_prompt: str) -> str:
        inferred_date = str(self._inferred_prompt_cache.get("target_date", "") or "")
        inferred_prompt = str(self._inferred_prompt_cache.get("prompt", "") or "").strip()
        if inferred_date == today and inferred_prompt:
            # 推断是目标日期的补充策略，不能覆盖用户配置的持续方向。
            supplement = (
                f"【{today} 当天补充策略】\n{inferred_prompt}\n"
                f"以上策略仅适用于{today}当天；其中相对日期以目标日为准，"
                "不得据此将当天任务顺延；与固定要求冲突时优先固定要求。"
            )
            fixed = fallback_prompt.strip()
            return f"{fixed}\n\n{supplement}" if fixed else supplement
        return fallback_prompt

    async def _schedule_loop(self):
        """
        定时任务循环

        持续运行的异步循环，计算下次执行时间并等待。
        当到达配置的时间点时，自动执行日程生成任务。

        循环会处理异常情况，确保单次失败不会影响后续执行。
        """
        while self.is_running:
            try:
                now = self.tz_manager.get_now()
                schedule_enabled = self.plugin.config.schedule.auto_schedule_enabled
                schedule_time_str = self.plugin.config.schedule.auto_schedule_time
                infer_enabled = bool(
                    self.plugin.config.schedule.auto_infer_next_day_prompt
                )
                infer_time_str = self.plugin.config.schedule.infer_time
                today_str = now.strftime("%Y-%m-%d")

                ran_any_task = False

                if infer_enabled and self._is_time_due(now, infer_time_str, self._last_infer_date):
                    # v4.4.4：使用 plugin.build_schedule_config 统一构建（含 bot_profile），
                    # 不再用 schedule.model_dump()（会漏掉 bot_profile 字段）
                    await self._engine.prepare_generation()
                    schedule_config = self._engine.build_generator_config()
                    await self._infer_next_day_prompt(schedule_config)
                    self._last_infer_date = today_str
                    ran_any_task = True

                if schedule_enabled and self._is_time_due(now, schedule_time_str, self._last_schedule_date):
                    await self._generate_today_schedule()
                    self._last_schedule_date = today_str
                    self._retry_count = 0
                    ran_any_task = True

                if ran_any_task:
                    continue

                wait_candidates = []
                if schedule_enabled:
                    wait_candidates.append(self._seconds_until_next(now, schedule_time_str, self._last_schedule_date))
                if infer_enabled:
                    wait_candidates.append(self._seconds_until_next(now, infer_time_str, self._last_infer_date))
                wait_seconds = max(15.0, min(60.0, min(wait_candidates, default=60.0)))
                await asyncio.sleep(wait_seconds)

            except asyncio.CancelledError:
                break
            except Exception as e:
                self.logger.error(f"定时任务出错: {e}", exc_info=True)
                # P1优化：指数退避重试（30s, 60s, 120s, 240s, 300s）
                self._retry_count += 1
                wait_time = min(30 * (2 ** (self._retry_count - 1)), self._max_retry_wait)
                self.logger.info(f"将在 {wait_time} 秒后重试（第 {self._retry_count} 次）")
                await asyncio.sleep(wait_time)

    async def _generate_today_schedule(self):
        """串行生成并应用后刷新快照，与命令和工具共享同一写入锁。"""
        async with self._engine.mutation_lock:
            await self._generate_today_schedule_unlocked()
            await self._engine.refresh(force=True)

    async def _generate_today_schedule_unlocked(self):
        """
        生成今日日程

        定时任务的核心执行方法，自动生成今天的日程。
        完全静默运行，不发送任何消息到聊天，只记录日志。

        生成成功后会自动保存为目标，供后续使用。
        """
        try:
            # ✅ 使用时区管理器获取今天日期
            today = self.tz_manager.get_now().strftime("%Y-%m-%d")
            self.logger.info(f"🔄 开始自动生成今日日程: {today}")

            # 检查今天是否已有日程（修复：支持datetime对象）
            goal_manager = self._engine.goal_manager
            goals = await self._engine.run_db(goal_manager.get_all_goals, chat_id="global")

            today_has_schedule = False
            today_schedule_count = 0

            for goal in goals:
                # 检查目标是否有time_window（日程类型）
                time_window = None
                if goal.parameters and "time_window" in goal.parameters:
                    time_window = goal.parameters["time_window"]
                elif goal.conditions and "time_window" in goal.conditions:
                    time_window = goal.conditions["time_window"]

                # 如果有time_window且创建时间是今天，说明已有日程
                if time_window:
                    created_at = goal.created_at
                    goal_date = None

                    # 支持字符串和datetime对象
                    if isinstance(created_at, str):
                        goal_date = created_at.split("T")[0] if "T" in created_at else created_at[:10]
                    elif created_at:
                        goal_date = created_at.strftime("%Y-%m-%d")

                    if goal_date == today:
                        today_has_schedule = True
                        today_schedule_count += 1

            if today_has_schedule:
                self.logger.info(f"📅 今日已有 {today_schedule_count} 个日程，跳过自动生成")
                return

            # 生成日程
            # v4.4.4：使用 plugin.build_schedule_config 统一构建（含 bot_profile），
            # 不再用 schedule.model_dump()（会漏掉 bot_profile 字段，导致 prompt
            # 中"未配置或为空"报错）
            await self._engine.prepare_generation()
            schedule_config = self._engine.build_generator_config()
            fallback_prompt = str(schedule_config.get("custom_prompt", "") or "")
            effective_prompt = self._get_effective_custom_prompt(today, fallback_prompt)
            if effective_prompt != fallback_prompt:
                schedule_config["custom_prompt"] = effective_prompt
                self.logger.info("已应用次日推断策略到今日日程生成")

            schedule_generator = self.ScheduleGenerator(
                goal_manager=goal_manager,
                config=schedule_config,
                plugin=self.plugin,  # v4: 注入 plugin 引用以便用 ctx.llm.generate
                run_db=self._engine.run_db,
            )

            schedule = await schedule_generator.generate_daily_schedule(
                user_id="system",
                chat_id="global",
                preferences={},
                use_llm=True,
                use_multi_round=self.plugin.config.schedule.use_multi_round,
            )

            # 🔧 修复：如果日程已存在，跳过应用
            if schedule.metadata and schedule.metadata.get("existing"):
                self.logger.info(f"📅 今天已有日程（{len(schedule.items)}个活动），跳过应用")
                return

            # 应用日程
            created_ids = await schedule_generator.apply_schedule(
                schedule=schedule, user_id="system", chat_id="global", auto_start=True
            )

            if created_ids:
                self.logger.info(f"✅ 自动生成日程成功: {today} - 创建了 {len(created_ids)} 个活动")
            else:
                raise RuntimeError(f"自动生成日程失败: {today}，没有创建任何活动")

        except Exception as e:
            self.logger.error(f"自动生成日程出错: {e}", exc_info=True)
            raise
