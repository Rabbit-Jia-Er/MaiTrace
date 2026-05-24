"""MaiTrace Routine 模式 - 日程驱动统一行为管理。

通过跨插件 API 调用 ``xuqian13.autonomous-planning-plugin-v4.get_current_activity``
取得当前日程活动，由 LLM 决策是否发说说 / 刷空间，到点触发日记生成。

由 plugin.py on_load 创建并启动，on_unload 停止。
"""

from __future__ import annotations

import asyncio
import collections
import datetime
import logging
from ..utils import get_logger
import random
import time
import traceback
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, List, Optional

from ..utils import peel_envelope
from ..utils.time_window import is_in_silent_period
from .feed_publish import send_feed
from .llm_runner import LLMRunner
from .monitor import run_browse_once
from .persona import resolve_persona

logger = get_logger(__name__)


# ============================================================
# 日程适配层
# ============================================================


class ActivityType(Enum):
    SLEEPING = "sleeping"
    WAKING_UP = "waking_up"
    EATING = "eating"
    WORKING = "working"
    STUDYING = "studying"
    EXERCISING = "exercising"
    RELAXING = "relaxing"
    SOCIALIZING = "socializing"
    COMMUTING = "commuting"
    HOBBY = "hobby"
    SELF_CARE = "self_care"
    OTHER = "other"


@dataclass
class ActivityInfo:
    activity_type: ActivityType
    description: str
    mood: str = "neutral"
    time_point: str = ""


_TYPE_MAP: dict[str, ActivityType] = {
    # 英文
    "work": ActivityType.WORKING,
    "study": ActivityType.STUDYING,
    "exercise": ActivityType.EXERCISING,
    "eat": ActivityType.EATING,
    "meal": ActivityType.EATING,
    "rest": ActivityType.RELAXING,
    "relax": ActivityType.RELAXING,
    "social": ActivityType.SOCIALIZING,
    "hobby": ActivityType.HOBBY,
    "sleep": ActivityType.SLEEPING,
    "self_care": ActivityType.SELF_CARE,
    "commut": ActivityType.COMMUTING,
    # 中文
    "工作": ActivityType.WORKING, "办公": ActivityType.WORKING, "会议": ActivityType.WORKING,
    "学习": ActivityType.STUDYING, "阅读": ActivityType.STUDYING, "读书": ActivityType.STUDYING,
    "审阅": ActivityType.STUDYING, "看书": ActivityType.STUDYING, "研究": ActivityType.STUDYING,
    "运动": ActivityType.EXERCISING, "锻炼": ActivityType.EXERCISING,
    "健身": ActivityType.EXERCISING, "散步": ActivityType.EXERCISING,
    "吃": ActivityType.EATING, "餐": ActivityType.EATING,
    "料理": ActivityType.EATING, "烹饪": ActivityType.EATING,
    "休息": ActivityType.RELAXING, "放松": ActivityType.RELAXING,
    "泡澡": ActivityType.RELAXING, "泡浴": ActivityType.RELAXING,
    "聊天": ActivityType.SOCIALIZING, "交流": ActivityType.SOCIALIZING, "社交": ActivityType.SOCIALIZING,
    "睡": ActivityType.SLEEPING, "梦": ActivityType.SLEEPING,
    "入眠": ActivityType.SLEEPING, "午休": ActivityType.SLEEPING, "小憩": ActivityType.SLEEPING,
    "梳妆": ActivityType.SELF_CARE, "打扮": ActivityType.SELF_CARE,
    "化妆": ActivityType.SELF_CARE, "护肤": ActivityType.SELF_CARE,
    "通勤": ActivityType.COMMUTING, "赶路": ActivityType.COMMUTING, "出行": ActivityType.COMMUTING,
    "起床": ActivityType.WAKING_UP, "醒": ActivityType.WAKING_UP,
}


def _classify_activity(goal_type: str, description: str) -> ActivityType:
    combined = (goal_type + " " + description).lower()
    for key, atype in _TYPE_MAP.items():
        if key in combined:
            return atype
    return ActivityType.OTHER


# ============================================================
# PlanningPluginProvider - 通过 xuqian13.autonomous-planning-plugin-v4 API
# ============================================================


class PlanningPluginProvider:
    """通过自主规划插件 v4 的公开 API 取当前活动。

    依赖：xuqian13.autonomous-planning-plugin-v4 已安装并暴露 get_current_activity。
    返回 None 时调用方按"无活动"处理。
    """

    PLUGIN_API = "xuqian13.autonomous-planning-plugin-v4.get_current_activity"
    _MISSING_THRESHOLD = 5  # 连续 N 次 API 不可达后降级到 debug 日志

    def __init__(self, plugin) -> None:
        self.plugin = plugin
        self._missing_count = 0
        self._last_snapshot: Optional[Dict[str, Any]] = None

    async def get_current_activity(self) -> Optional[ActivityInfo]:
        try:
            result = await self.plugin.ctx.api.call(self.PLUGIN_API, chat_id="global")
        except PermissionError as exc:
            self._missing_count += 1
            if self._missing_count <= self._MISSING_THRESHOLD:
                logger.info("规划插件 API 权限不足（可能未安装）: %s", exc)
            return None
        except Exception as exc:
            self._missing_count += 1
            level = logger.warning if self._missing_count <= self._MISSING_THRESHOLD else logger.debug
            level("规划插件 API 调用失败: %s", exc)
            return None

        result = peel_envelope(result)
        if not isinstance(result, dict):
            return None

        self._last_snapshot = result
        self._missing_count = 0

        if not result.get("has_activity"):
            return None
        activity = result.get("activity") or {}
        if not isinstance(activity, dict):
            return None

        description = activity.get("description") or activity.get("name") or "日常活动"
        goal_type = str(activity.get("goal_type") or "")
        time_window = str(activity.get("time_window") or "")
        time_point = time_window or datetime.datetime.now().strftime("%H:%M")
        return ActivityInfo(
            activity_type=_classify_activity(goal_type, description),
            description=str(description),
            mood="neutral",
            time_point=time_point,
        )

    def get_last_snapshot(self) -> Optional[Dict[str, Any]]:
        """供调试命令展示 API 最近一次原始返回。"""
        return dict(self._last_snapshot) if self._last_snapshot else None


# ============================================================
# RoutineRunner
# ============================================================


class RoutineRunner:
    """日程驱动的统一行为管理器。"""

    def __init__(self, plugin) -> None:
        self.plugin = plugin
        self.schedule_provider: Optional[PlanningPluginProvider] = None
        self.is_running = False
        self.task: Optional[asyncio.Task] = None
        self.last_post_time: float = 0
        self.last_browse_time: float = 0
        self.last_diary_date: Optional[datetime.date] = None
        self._last_check_ts: float = 0.0
        # /zn debug routine 用：最近 N 次 LLM 决策与执行结果
        self._decision_history: collections.deque[dict] = collections.deque(maxlen=20)

    def get_decision_history(self) -> List[dict]:
        """返回最近 N 次决策记录（最新在末尾）。"""
        return list(self._decision_history)

    def get_planning_snapshot(self) -> Optional[Dict[str, Any]]:
        """返回规划插件最近一次返回的活动快照。"""
        if self.schedule_provider is None:
            return None
        return self.schedule_provider.get_last_snapshot()

    async def start(self) -> None:
        if self.is_running:
            return
        self.schedule_provider = PlanningPluginProvider(self.plugin)
        self.is_running = True
        self.task = asyncio.create_task(self._loop())
        logger.info("Routine 模式已启动（通过 %s）", PlanningPluginProvider.PLUGIN_API)

    async def stop(self) -> None:
        if not self.is_running:
            return
        self.is_running = False
        if self.task:
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass
        logger.info("Routine 模式已停止")

    # ---------- 主循环 ----------

    async def _loop(self) -> None:
        check_interval = self.plugin.config.routine.check_interval_minutes
        while self.is_running:
            try:
                activity = await self.schedule_provider.get_current_activity() if self.schedule_provider else None

                # 日记检查：无论是否有活动都要尝试（基于时间窗）
                await self._check_diary()

                if not activity:
                    logger.debug("routine: 无当前活动")
                    self._last_check_ts = time.time()
                    await asyncio.sleep(check_interval * 60)
                    continue

                logger.info("routine: 当前活动 [%s] %s", activity.activity_type.value, activity.description)

                if activity.activity_type == ActivityType.SLEEPING:
                    logger.debug("routine: 睡眠中，跳过发说说/刷空间")
                    self._last_check_ts = time.time()
                    await asyncio.sleep(check_interval * 60)
                    continue

                # 发说说
                if await self._llm_decide(activity, "post"):
                    try:
                        await self._post_feed(activity)
                    except Exception as exc:
                        logger.error("routine: 发说说失败: %s", exc)
                        traceback.print_exc()
                        self._mark_last_executed("post", False, str(exc))

                # 刷空间
                if await self._llm_decide(activity, "browse"):
                    try:
                        await self._browse_feeds()
                    except Exception as exc:
                        logger.error("routine: 刷空间失败: %s", exc)
                        traceback.print_exc()
                        self._mark_last_executed("browse", False, str(exc))

                self._last_check_ts = time.time()
                await asyncio.sleep(check_interval * 60)
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error("routine 主循环出错: %s", exc)
                traceback.print_exc()
                self._last_check_ts = time.time()
                await asyncio.sleep(300)

    # ---------- LLM 决策 ----------

    @staticmethod
    def _parse_llm_decision(answer: str) -> tuple[bool, str]:
        """严格解析 LLM 输出。

        约定格式：``是|<理由>`` 或 ``否|<理由>``。只看第一个非空白字符：
        ``是`` → True；``否`` → False；其他 → False（保守拒绝）。

        Returns:
            (decision, reason)
        """
        clean = (answer or "").strip().strip("`'\"").strip()
        if not clean:
            return False, ""
        first = clean[0]
        # 取 | 后面作为 reason（如果有）；没分隔符就取整段去掉首字符
        if "|" in clean:
            _, _, reason = clean.partition("|")
        elif "｜" in clean:  # 中文全角
            _, _, reason = clean.partition("｜")
        elif ":" in clean or "：" in clean:
            sep = ":" if ":" in clean else "："
            _, _, reason = clean.partition(sep)
        else:
            reason = clean[1:].lstrip("，,。.！!？? \t")
        reason = (reason or "").strip()[:80]

        if first == "是":
            return True, reason
        if first == "否":
            return False, reason
        # 不是"是/否"开头一律视为否
        return False, f"格式异常: {clean[:30]}"

    def _hard_rules_block(
        self,
        activity: ActivityInfo,
        action: str,
    ) -> Optional[str]:
        """LLM 调用前的硬规则。返回非空字符串表示被拦截，字符串是拒绝原因。"""
        cfg = self.plugin.config.routine
        cfg_monitor = self.plugin.config.monitor

        # 1. 静默时段（复用 monitor.silent_hours）
        if cfg.respect_silent_hours and cfg_monitor.silent_hours:
            is_silent, _, _ = is_in_silent_period(
                cfg_monitor.silent_hours,
                like_during_silent=False,
                comment_during_silent=False,
            )
            if is_silent:
                return f"静默时段({cfg_monitor.silent_hours})"

        # 2. 活动类型黑名单
        blocked = cfg.post_blocked_activities if action == "post" else cfg.browse_blocked_activities
        blocked_lower = {str(b).strip().lower() for b in (blocked or []) if str(b).strip()}
        if activity.activity_type.value in blocked_lower:
            return f"活动黑名单({activity.activity_type.value})"

        return None

    async def _llm_decide(self, activity: ActivityInfo, action: str) -> bool:
        cfg = self.plugin.config.routine

        # 0. 冷却硬限制
        if action == "post":
            cooldown = cfg.post_cooldown_minutes * 60
            if time.time() - self.last_post_time < cooldown:
                self._record_decision(
                    action=action, activity=activity,
                    llm_answer="", reason="冷却中", decision=False,
                    cooldown_skipped=True,
                )
                return False
        elif action == "browse":
            cooldown = cfg.browse_cooldown_minutes * 60
            if time.time() - self.last_browse_time < cooldown:
                self._record_decision(
                    action=action, activity=activity,
                    llm_answer="", reason="冷却中", decision=False,
                    cooldown_skipped=True,
                )
                return False

        # 1. 硬规则（静默时段 + 活动黑名单），命中直接拒，不调 LLM
        block_reason = self._hard_rules_block(activity, action)
        if block_reason:
            self._record_decision(
                action=action, activity=activity,
                llm_answer="", reason=block_reason, decision=False,
                cooldown_skipped=False, hard_blocked=True,
            )
            logger.info("routine: 硬规则拒绝 %s (%s)", action, block_reason)
            return False

        # 2. LLM 决策
        persona = await resolve_persona(self.plugin)
        current_time = datetime.datetime.now().strftime("%H:%M")
        action_desc = "发一条 QQ 空间说说" if action == "post" else "刷一下 QQ 空间看看好友动态"
        require_reason = cfg.require_reason

        if require_reason:
            format_hint = (
                "请用如下格式回答（严格）：\n"
                "  是|<不超过20字的简短理由>\n"
                "  或\n"
                "  否|<不超过20字的简短理由>\n"
                "第一个字符必须是'是'或'否'。"
            )
            max_tokens = 60
        else:
            format_hint = "只回答'是'或'否'，不要输出其他内容。第一个字符必须是'是'或'否'。"
            max_tokens = 6

        prompt = (
            f"你是'{persona.personality}'。当前时间 {current_time}，你正在 {activity.description}。\n\n"
            f"请判断你现在是否会自然地{action_desc}。\n\n"
            "【绝对不会的情况】\n"
            "- 正在专注做事（工作 / 学习 / 会议 / 写代码 / 审阅）\n"
            "- 正在用餐、洗澡、运动、化妆等需要专注或不方便看手机的活动\n"
            "- 正在睡觉，或刚睡醒还没清醒\n"
            "- 时间太深夜（22:00 后到次日 8:00）\n"
            "- 当前活动与'低头看手机/打字发消息'明显冲突\n"
            "- 最近刚做过同类事（哪怕没明确告诉你）\n\n"
            "【才可以的情况】\n"
            "- 当前是放松 / 休息 / 闲逛 / 通勤 / 闲聊等碎片时间\n"
            "- 时间是白天或晚上 8-10 点等正常社交活跃时段\n"
            "- 你确实有想分享的内容或被好奇心驱动\n\n"
            "绝大多数情况下应该回答'否'——能回答'否'就回答'否'。\n\n"
            f"{format_hint}"
        )

        runner = LLMRunner(
            self.plugin.ctx,
            self.plugin.config.llm.text_model,
            timeout=self.plugin.config.llm.llm_timeout_seconds,
        )
        success, answer = await runner.generate(prompt, temperature=0.2, max_tokens=max_tokens)
        if not success:
            self._record_decision(
                action=action, activity=activity,
                llm_answer=f"(LLM 失败: {answer})", reason="LLM 调用失败",
                decision=False, cooldown_skipped=False,
            )
            return False

        decision, reason = self._parse_llm_decision(answer)
        logger.debug("routine: LLM 决策 %s -> answer=%r -> decision=%s reason=%s",
                     action, answer, decision, reason)

        # 3. 二次掷骰（max_*_chance < 1.0 时生效）
        dice_skipped = False
        if decision:
            max_chance = cfg.max_post_chance if action == "post" else cfg.max_browse_chance
            if max_chance < 1.0 and random.random() > max_chance:
                decision = False
                dice_skipped = True
                logger.info("routine: %s 二次掷骰跳过 (max_chance=%.2f)", action, max_chance)

        self._record_decision(
            action=action, activity=activity,
            llm_answer=answer, reason=reason, decision=decision,
            cooldown_skipped=False, dice_skipped=dice_skipped,
        )
        return decision

    def _record_decision(
        self,
        *,
        action: str,
        activity: ActivityInfo,
        llm_answer: str,
        decision: bool,
        cooldown_skipped: bool,
        reason: str = "",
        hard_blocked: bool = False,
        dice_skipped: bool = False,
    ) -> None:
        self._decision_history.append({
            "ts": time.time(),
            "action": action,
            "activity_type": activity.activity_type.value,
            "activity_desc": activity.description,
            "llm_answer": (llm_answer or "")[:200],
            "reason": (reason or "")[:80],
            "decision": decision,
            "cooldown_skipped": cooldown_skipped,
            "hard_blocked": hard_blocked,
            "dice_skipped": dice_skipped,
            "executed": None,
            "error": "",
        })

    def _mark_last_executed(self, action: str, executed: bool, error: str = "") -> None:
        """回写最近一条匹配 action 的决策的 executed/error 字段。"""
        for entry in reversed(self._decision_history):
            if entry["action"] == action and entry["executed"] is None:
                entry["executed"] = executed
                entry["error"] = (error or "")[:200]
                return

    # ---------- 发说说 ----------

    async def _post_feed(self, activity: ActivityInfo) -> None:
        logger.info("routine: 准备发说说，活动: %s", activity.description)
        success, story = await send_feed(
            self.plugin,
            activity.description,
            current_activity=activity.description,
        )
        if success:
            self.last_post_time = time.time()
            logger.info("routine: 发说说成功: %s", story)
            self._mark_last_executed("post", True)
        else:
            logger.error("routine: 发说说失败: %s", story)
            self._mark_last_executed("post", False, story)

    # ---------- 刷空间 ----------

    async def _browse_feeds(self) -> None:
        logger.info("routine: 准备刷空间")
        success, msg = await run_browse_once(self.plugin)
        if success:
            self.last_browse_time = time.time()
            logger.info("routine: 刷空间完成: %s", msg)
            self._mark_last_executed("browse", True)
        else:
            logger.warning("routine: 刷空间结果: %s", msg)
            self._mark_last_executed("browse", False, msg)

    # ---------- 日记 ----------

    async def _check_diary(self) -> None:
        """到点触发日记生成。

        用"上次检查时间戳 → 现在"这个窗口跨越 schedule_time 判定，避免 20 分钟轮询永远错过的问题。
        当天已生成过则跳过；首次启动若已过 schedule_time，也会触发一次。
        """
        cfg = self.plugin.config.diary
        if not cfg.enabled:
            return

        schedule_str = (cfg.schedule_time or "").strip()
        if ":" not in schedule_str:
            return
        try:
            hh_s, mm_s = schedule_str.split(":", 1)
            hh, mm = int(hh_s.strip()), int(mm_s.strip())
        except ValueError:
            logger.warning("diary.schedule_time 格式错误: %s", schedule_str)
            return
        if not (0 <= hh <= 23 and 0 <= mm <= 59):
            return

        now = datetime.datetime.now()
        today = now.date()
        if self.last_diary_date == today:
            return

        target = datetime.datetime.combine(today, datetime.time(hh, mm))
        target_ts = target.timestamp()
        now_ts = now.timestamp()

        if self._last_check_ts > 0:
            # 后续循环：上次检查 → 现在的窗口跨过了 target
            crossed = self._last_check_ts <= target_ts < now_ts
        else:
            # 首次循环：只要当前已过 target（同一天内），也触发一次
            crossed = now >= target

        if not crossed:
            return

        self.last_diary_date = today
        logger.info("routine: 到达日记生成时间 target=%s now=%s",
                    target.strftime("%H:%M"), now.strftime("%H:%M:%S"))
        asyncio.create_task(self._generate_diary())

    async def _generate_diary(self) -> None:
        try:
            from .diary.pipeline import DiaryPipeline
            pipeline = DiaryPipeline(self.plugin)
            await pipeline.generate_and_publish_for_today()
        except Exception as exc:
            logger.error("routine: 日记生成失败: %s", exc)
            traceback.print_exc()
