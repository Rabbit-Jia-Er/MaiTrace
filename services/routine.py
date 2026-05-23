"""MaiTrace Routine 模式 - 日程驱动统一行为管理。

读取 autonomous_planning_plugin 的 SQLite 数据库取当前活动，
由 LLM 决策是否发说说 / 刷空间，到点触发日记生成。

由 plugin.py on_load 创建并启动，on_unload 停止。
"""

from __future__ import annotations

import asyncio
import collections
import datetime
import json
import logging
import os
import sqlite3
import time
import traceback
from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, List, Optional

from ..utils import peel_envelope
from .feed_publish import send_feed
from .llm_runner import LLMRunner
from .monitor import run_browse_once

logger = logging.getLogger(__name__)


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


def _row_to_activity(row: Dict[str, Any], current_time: str) -> ActivityInfo:
    description = row.get("description") or row.get("name") or "日常活动"
    goal_type = (row.get("goal_type") or "").lower()
    return ActivityInfo(
        activity_type=_classify_activity(goal_type, description),
        description=description,
        mood="neutral",
        time_point=current_time,
    )


# ============================================================
# PlanningPluginProvider - 直接读 autonomous_planning SQLite
# ============================================================


class PlanningPluginProvider:
    """从 autonomous_planning_plugin/data/*.db 读当前活动。"""

    def __init__(self, db_path: str) -> None:
        self.db_path = db_path
        logger.info("PlanningPluginProvider 初始化, db: %s", db_path)

    async def get_current_activity(self) -> Optional[ActivityInfo]:
        try:
            if not os.path.exists(self.db_path):
                return None
            conn = sqlite3.connect(self.db_path)
            conn.row_factory = sqlite3.Row
            try:
                cursor = conn.cursor()
                now = datetime.datetime.now()
                current_time_str = now.strftime("%H:%M")
                today_str = now.strftime("%Y-%m-%d")
                current_minutes = now.hour * 60 + now.minute

                rows: List[Any] = []
                try:
                    cursor.execute(
                        "SELECT * FROM goals "
                        "WHERE status = 'active' AND substr(created_at, 1, 10) = ? "
                        "ORDER BY created_at DESC LIMIT 20",
                        (today_str,),
                    )
                    rows = cursor.fetchall()
                except sqlite3.Error as exc:
                    logger.debug("查询 goals 失败: %s", exc)
            finally:
                conn.close()

            if not rows:
                return None

            for row in rows:
                row_dict = dict(row)
                time_window = self._extract_time_window(row_dict)
                if time_window and len(time_window) == 2:
                    start_min, end_min = int(time_window[0]), int(time_window[1])
                    if self._in_minutes_range(current_minutes, start_min, end_min):
                        return _row_to_activity(row_dict, current_time_str)

            return _row_to_activity(dict(rows[0]), current_time_str)
        except Exception as exc:
            logger.error("PlanningPluginProvider 查询失败: %s", exc)
            return None

    @staticmethod
    def _extract_time_window(row: Dict[str, Any]) -> Optional[List[int]]:
        params_raw = row.get("parameters")
        if not params_raw:
            return None
        try:
            params = json.loads(params_raw) if isinstance(params_raw, str) else params_raw
            return params.get("time_window")
        except (json.JSONDecodeError, TypeError):
            return None

    @staticmethod
    def _in_minutes_range(current: int, start: int, end: int) -> bool:
        if end < start:
            return current >= start or current <= end
        return start <= current <= end


def _find_planning_db() -> Optional[str]:
    """搜索 autonomous_planning_plugin 数据库。"""
    plugins_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # plugins/
    parent_plugins = os.path.dirname(plugins_dir)  # plugins/ 的父级（MaiTrace 的兄弟目录）
    # 我们在 plugins/MaiTrace/services/routine.py，所以 plugins_dir 实际是 plugins/MaiTrace/，
    # 真正的 plugins/ 在它的父目录
    real_plugins_dir = os.path.dirname(plugins_dir)
    search_dirs = [
        os.path.join(real_plugins_dir, "autonomous_planning_plugin"),
        os.path.join(real_plugins_dir, "autonomous_planning"),
    ]
    for sd in search_dirs:
        if not os.path.isdir(sd):
            continue
        for check in (sd, os.path.join(sd, "data")):
            if not os.path.isdir(check):
                continue
            for fname in os.listdir(check):
                if not fname.endswith((".db", ".sqlite", ".sqlite3")):
                    continue
                db_path = os.path.join(check, fname)
                try:
                    conn = sqlite3.connect(db_path)
                    try:
                        cursor = conn.cursor()
                        cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='goals'")
                        if cursor.fetchone():
                            logger.info("找到 autonomous_planning 数据库: %s", db_path)
                            return db_path
                    finally:
                        conn.close()
                except sqlite3.Error:
                    continue
    return None


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
        # /zn debug routine 用：最近 N 次 LLM 决策与执行结果
        self._decision_history: collections.deque[dict] = collections.deque(maxlen=20)

    def get_decision_history(self) -> List[dict]:
        """返回最近 N 次决策记录（最新在末尾）。"""
        return list(self._decision_history)

    async def start(self) -> None:
        if self.is_running:
            return
        db_path = _find_planning_db()
        if not db_path:
            logger.warning("未找到 autonomous_planning 数据库，Routine 模式无法启动")
            return
        self.schedule_provider = PlanningPluginProvider(db_path)
        self.is_running = True
        self.task = asyncio.create_task(self._loop())
        logger.info("Routine 模式已启动")

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
                if not activity:
                    logger.debug("routine: 无当前活动")
                    await asyncio.sleep(check_interval * 60)
                    continue

                logger.info("routine: 当前活动 [%s] %s", activity.activity_type.value, activity.description)

                if activity.activity_type == ActivityType.SLEEPING:
                    logger.debug("routine: 睡眠中，跳过")
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

                # 日记
                await self._check_diary()

                await asyncio.sleep(check_interval * 60)
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.error("routine 主循环出错: %s", exc)
                traceback.print_exc()
                await asyncio.sleep(300)

    # ---------- LLM 决策 ----------

    async def _llm_decide(self, activity: ActivityInfo, action: str) -> bool:
        # 冷却硬限制
        cfg = self.plugin.config.routine
        if action == "post":
            cooldown = cfg.post_cooldown_minutes * 60
            if time.time() - self.last_post_time < cooldown:
                self._record_decision(
                    action=action, activity=activity,
                    llm_answer="", decision=False, cooldown_skipped=True,
                )
                return False
        elif action == "browse":
            cooldown = cfg.browse_cooldown_minutes * 60
            if time.time() - self.last_browse_time < cooldown:
                self._record_decision(
                    action=action, activity=activity,
                    llm_answer="", decision=False, cooldown_skipped=True,
                )
                return False

        bot_personality = await _get_global(self.plugin.ctx, "personality.personality", "")
        current_time = datetime.datetime.now().strftime("%H:%M")
        action_desc = "发一条 QQ 空间说说" if action == "post" else "刷一下 QQ 空间看看好友动态"
        prompt = (
            f"你是'{bot_personality}'，现在是{current_time}，你正在{activity.description}。\n"
            f"请判断你现在是否会自然地{action_desc}。\n"
            f"要求非常严格：只有在当前活动和时间确实适合的情况下才回答'是'。\n"
            f"大部分情况下应该回答'否'——正在专注做事、睡觉、忙碌时不会刷手机。\n"
            f"只回答'是'或'否'，不要输出其他内容。"
        )

        runner = LLMRunner(
            self.plugin.ctx,
            self.plugin.config.llm.text_model,
            timeout=self.plugin.config.llm.llm_timeout_seconds,
        )
        success, answer = await runner.generate(prompt, temperature=0.3, max_tokens=10)
        if not success:
            self._record_decision(
                action=action, activity=activity,
                llm_answer=f"(LLM 失败: {answer})", decision=False, cooldown_skipped=False,
            )
            return False
        decision = "是" in answer and "否" not in answer
        logger.debug("routine: LLM 决策 %s -> '%s' -> %s", action, answer, decision)
        self._record_decision(
            action=action, activity=activity,
            llm_answer=answer, decision=decision, cooldown_skipped=False,
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
    ) -> None:
        self._decision_history.append({
            "ts": time.time(),
            "action": action,
            "activity_type": activity.activity_type.value,
            "activity_desc": activity.description,
            "llm_answer": (llm_answer or "")[:200],
            "decision": decision,
            "cooldown_skipped": cooldown_skipped,
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
        cfg = self.plugin.config.diary
        if not cfg.enabled:
            return
        current = datetime.datetime.now().strftime("%H:%M")
        if current != cfg.schedule_time:
            return
        today = datetime.datetime.now().date()
        if self.last_diary_date == today:
            return
        self.last_diary_date = today
        logger.info("routine: 到达日记生成时间")
        asyncio.create_task(self._generate_diary())

    async def _generate_diary(self) -> None:
        try:
            from .diary.pipeline import DiaryPipeline
            pipeline = DiaryPipeline(self.plugin)
            await pipeline.generate_and_publish_for_today()
        except Exception as exc:
            logger.error("routine: 日记生成失败: %s", exc)
            traceback.print_exc()


# ===== helpers =====


async def _get_global(ctx, key: str, default: str = "") -> str:
    try:
        value = await ctx.config.get(key, default)
    except Exception as exc:
        logger.warning("ctx.config.get(%s) 异常: %s", key, exc)
        return default
    value = peel_envelope(value)
    if isinstance(value, dict):
        value = value.get("value", default)
    return str(value or default)
