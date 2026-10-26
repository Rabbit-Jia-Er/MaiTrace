"""MaiTrace Routine 模式 - 日程驱动统一行为管理。

读取所选日程提供方的内置格式快照和公开生活契机，由 LLM 决策是否发说说 / 刷空间，
只在有具体生活素材时考虑公开分享。

由 plugin.py on_load 创建并启动，on_unload 停止。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo
import asyncio
import collections
import datetime
import random
import time
import traceback

from ..utils import get_logger, wrap_untrusted
from ..utils.time_window import is_in_silent_period
from .activity import ActivityInfo, ActivityType, _classify_activity
from .feed_publish import send_feed
from .llm_runner import LLMRunner
from .limits import publication_budget_seconds
from .monitor import run_browse_once
from .persona import resolve_persona
from .qzone_api import PublicationState

logger = get_logger(__name__)


# ============================================================
# 日程适配层
# ============================================================


# ============================================================
# InternalScheduleProvider - 本实例的内部日程快照
# ============================================================


class InternalScheduleProvider:
    """读取当前日程来源的内置格式快照。

    来源由运行时选择；这里只读取已缓存的统一结构，不执行跨插件 RPC。
    """

    def __init__(self, plugin) -> None:
        self.plugin = plugin
        self._last_snapshot: Optional[Dict[str, Any]] = None

    async def get_current_activity(self) -> Optional[ActivityInfo]:
        if not self.plugin.config.schedule.enabled or self.plugin.schedule_engine is None:
            return None
        result = self.plugin.schedule_engine.current_activity_payload()
        if not isinstance(result, dict):
            raise TypeError("日程快照必须是字典")
        self._last_snapshot = result
        if result.get("error"):
            raise RuntimeError(f"日程查询失败: {result['error']}")

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
        """供调试命令展示所选来源最近读取的快照。"""
        return dict(self._last_snapshot) if self._last_snapshot else None


# ============================================================
# RoutineRunner
# ============================================================


class RoutineRunner:
    """日程驱动的统一行为管理器。"""

    def __init__(self, plugin) -> None:
        self.plugin = plugin
        self.schedule_provider: Optional[InternalScheduleProvider] = None
        self.is_running = False
        self.task: Optional[asyncio.Task] = None
        self.last_post_time: float = 0
        self.last_browse_time: float = 0
        self._last_check_ts: float = 0.0
        # /zn debug routine 用：最近 N 次 LLM 决策与执行结果
        self._decision_history: collections.deque[dict] = collections.deque(maxlen=20)

    def get_decision_history(self) -> List[dict]:
        """返回最近 N 次决策记录（最新在末尾）。"""
        return list(self._decision_history)

    def get_planning_snapshot(self) -> Optional[Dict[str, Any]]:
        """返回本实例从所选来源最近读取的日程快照。"""
        if self.schedule_provider is None:
            return None
        return self.schedule_provider.get_last_snapshot()

    def export_state(self) -> Dict[str, Any]:
        """热更新延续冷却与决策历史，防止重新装配重复触发。"""
        return {
            "last_post_time": self.last_post_time, "last_browse_time": self.last_browse_time,
            "last_check_ts": self._last_check_ts,
            "decision_history": list(self._decision_history),
        }

    def restore_state(self, state: Dict[str, Any]) -> None:
        if not state:
            return
        self.last_post_time = state["last_post_time"]
        self.last_browse_time = state["last_browse_time"]
        self._last_check_ts = state["last_check_ts"]
        self._decision_history.extend(state["decision_history"])

    async def start(self) -> None:
        if self.is_running:
            return
        self.schedule_provider = InternalScheduleProvider(self.plugin)
        self.is_running = True
        self.task = asyncio.create_task(self._loop())
        logger.info("Routine 已启动（日程提供方=%s）", self.plugin.config.schedule.provider)

    async def stop(self) -> None:
        self.is_running = False
        tasks = [self.task] if self.task is not None else []
        for task in tasks:
            task.cancel()
        results = await asyncio.gather(*tasks, return_exceptions=True)
        self.task = None
        for result in results:
            if isinstance(result, Exception):
                logger.error("Routine 后台任务退出异常: %s", result, exc_info=result)
        logger.info("Routine 模式已停止")

    # ---------- 主循环 ----------

    async def _loop(self) -> None:
        check_interval = self.plugin.config.routine.check_interval_minutes
        while self.is_running:
            try:
                activity = await self.schedule_provider.get_current_activity() if self.schedule_provider else None

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
                if self.plugin.config.routine.post_enabled:
                    await self._try_post_feed(activity)

                # 刷空间
                if self.plugin.config.routine.browse_enabled and await self._llm_decide(activity, "browse"):
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
                now=datetime.datetime.now(ZoneInfo(self.plugin.config.schedule.timezone)),
            )
            if is_silent:
                return f"静默时段({cfg_monitor.silent_hours})"

        # 2. 活动类型黑名单
        blocked = cfg.post_blocked_activities if action == "post" else cfg.browse_blocked_activities
        blocked_lower = {str(b).strip().lower() for b in (blocked or []) if str(b).strip()}
        if activity.activity_type.value in blocked_lower:
            return f"活动黑名单({activity.activity_type.value})"

        return None

    async def _llm_decide(
        self, activity: ActivityInfo, action: str, *, opportunity: Optional[Dict[str, Any]] = None,
    ) -> bool:
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
        current_time = datetime.datetime.now(ZoneInfo(self.plugin.config.schedule.timezone)).strftime("%H:%M")
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
        else:
            format_hint = "只回答'是'或'否'，不要输出其他内容。第一个字符必须是'是'或'否'。"

        prompt = (
            f"你是'{persona.personality}'。当前时间 {current_time}。\n"
            f"{wrap_untrusted(activity.description, label='当前活动')}\n"
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
        if opportunity is not None:
            prompt += wrap_untrusted(
                f"主题：{opportunity['topic']}\n动机：{opportunity.get('motive', '')}",
                label="本轮可公开分享的生活素材", max_chars=2000,
            )

        runner = LLMRunner(
            self.plugin.ctx,
            self.plugin.config.llm.text_model,
            timeout=self.plugin.config.llm.llm_timeout_seconds,
        )
        success, answer = await runner.generate(prompt, temperature=0.2, max_tokens=8192)
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

    async def _try_post_feed(self, activity: ActivityInfo) -> None:
        """同一调度入口消费公开契机；明确失败释放，发布结果不明则隔离。"""
        claim = None
        life = self.plugin.life_engine
        if life is None:
            return
        # 冷却和作息先筛选；无具体公开素材时不调用决策模型，也不空编近况。
        if time.time() - self.last_post_time < self.plugin.config.routine.post_cooldown_minutes * 60:
            self._record_decision(
                action="post", activity=activity, llm_answer="", reason="冷却中",
                decision=False, cooldown_skipped=True,
            )
            return
        block_reason = self._hard_rules_block(activity, "post")
        if block_reason:
            self._record_decision(
                action="post", activity=activity, llm_answer="", reason=block_reason,
                decision=False, cooldown_skipped=False, hard_blocked=True,
            )
            return
        completed = False
        confirmed = False
        publication = PublicationState()
        failure = "本轮未通过发布判断"
        try:
            if life is not None:
                lease = max(self.plugin.config.life.claim_lease_seconds, publication_budget_seconds(self.plugin.config) + 120)
                claim = await life.claim_public_opportunity(consumer="qzone", lease_seconds=lease)
            if claim is None:
                return
            if not await self._llm_decide(activity, "post", opportunity=claim):
                return
            completed, failure = await self._post_feed(activity, opportunity=claim, publication=publication)
            if completed and claim is not None:
                confirmed = await life.confirm(claim["claim_token"], receipt=publication.tid or "qzone_published")
                if not confirmed:
                    logger.error("空间已发布，但素材确认未成功: %s", claim["id"])
        except asyncio.CancelledError:
            failure = "发布任务取消"
            raise
        except Exception as exc:
            failure = str(exc)
            logger.exception("routine: 发说说失败")
            self._mark_last_executed("post", False, failure)
        finally:
            if publication.uncertain:
                # 结果待核对的发布也要避免紧接着重复触发；生图失败不会进入此分支。
                self.last_post_time = time.time()
            if claim is not None:
                if completed and not confirmed:
                    await life.quarantine(claim["claim_token"], error="空间已发布，但素材确认未成功")
                elif publication.uncertain:
                    await life.quarantine(claim["claim_token"], error=failure)
                    logger.error("素材发布结果待核对，已停止自动重发: %s", claim["id"])
                elif not completed:
                    await life.release(claim["claim_token"], error=failure)

    async def _post_feed(
        self, activity: ActivityInfo, *, opportunity: Optional[Dict[str, Any]] = None,
        publication: Optional[PublicationState] = None,
    ) -> Tuple[bool, str]:
        logger.info("routine: 准备发说说，活动: %s", activity.description)
        success, story = await send_feed(
            self.plugin,
            str(opportunity["topic"]) if opportunity is not None else activity.description,
            current_activity=activity.description,
            activity_type=activity.activity_type.value,
            publication=publication,
            material_instruction=str(opportunity.get("instruction", "")) if opportunity is not None else "",
            # 生活素材只是主题，不能因恰好等于 custom 而执行私聊直发命令。
            interpret_custom=False,
        )
        if success:
            self.last_post_time = time.time()
            logger.info("routine: 发说说成功: %s", story)
            self._mark_last_executed("post", True)
        else:
            logger.error("routine: 发说说失败: %s", story)
            self._mark_last_executed("post", False, story)
        return success, story

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
