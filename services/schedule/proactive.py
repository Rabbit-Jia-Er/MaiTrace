"""活动驱动的主动行为服务（v4.4 新增）。

三个职责由 MaiTrace 的统一周期调用：

1. **已确认约定到点主动开口** —— 通过 ``ctx.maisaka.trigger_proactive`` 触发
   Maisaka 跑一轮主动对话，履行已经接受的提醒或问候约定。

2. **普通日程主动聊天** —— 根据场景和实际状态判断自然话题，交给主程序 Planner 决定表达。

3. **按实际活动调节聊天频率** —— 通过
   ``ctx.frequency.set_adjust`` 把频率因子推给 heartflow，让 bot 在学习/工作时
   少说话、休息/娱乐时多说话。

两个能力都使用主程序提供的 SDK；调用失败记录原因，下一轮仍可重试。

设计要点：
    - 单独的 ``proactive_streams`` 白名单（默认空 = 完全禁用），避免误打扰
    - 每个 ``(stream_id, goal_id, date)`` 组合只主动触发一次
    - 频率因子按 goal_type 静态表映射，更新时记录避免重复 set_adjust
    - 不另建后台循环；卸载时取消在途巡检并恢复本服务施加的频率因子
"""

from datetime import datetime
from typing import TYPE_CHECKING, Any, Dict, List, Optional, Set, Tuple
import asyncio
import json
import logging
import time

from .utils.timezone_manager import TimezoneManager
from ..activity import ActivityType, _classify_activity
from ..llm_runner import LLMRunner
from ..life.models import json_object, required_text
from ..persistence import write_json
from ...utils import wrap_untrusted

if TYPE_CHECKING:
    from . import ScheduleEngine


logger = logging.getLogger(__name__)


# goal_type → 频率因子映射（1.0 = 主程序默认；<1 减少回复；>1 增加回复）
_FREQUENCY_FACTOR_BY_GOAL_TYPE: Dict[str, float] = {
    "study": 0.3,           # 学习：低频，不打扰
    "work": 0.3,            # 工作：低频
    "learn_topic": 0.5,     # 学习兴趣话题：偏低
    "sleep": 0.05,          # 睡觉：几乎不说话
    "exercise": 0.7,        # 运动：偏低
    "meal": 1.2,            # 吃饭：略高于平时
    "rest": 1.3,            # 休息：高频
    "daily_routine": 1.0,   # 日常作息：默认
    "entertainment": 1.5,   # 娱乐：高频
    "social_maintenance": 1.5,  # 社交：最高
    "sleeping": 0.05, "working": 0.3, "studying": 0.3, "exercising": 0.7,
    "eating": 1.2, "relaxing": 1.3, "socializing": 1.5, "hobby": 1.5,
    "self_care": 0.3, "bathing": 0.2,
}


# 已确认约定的 intent 模板，供 Maisaka 履行到点提醒
# v4.4.5：约定（pending_commitment）来源的活动用强指令模板，让 Maisaka 不要陷入
# "自行决定是否回复"的工具调用循环。占位符会在 _trigger_proactive 中填充。
_COMMITMENT_INTENT_TEMPLATE = (
    "你之前答应过对方今天{time_hint}要在当前会话中{title}（约定备注：{notes_hint}）。\n"
    "现在到点了，请用符合人设的语气**在当前会话中发出**这条消息（例如叫醒、问候、"
    "提醒等），不要询问对方、不要等用户先开口、不要陷入工具调用循环。\n"
    "活动名：{activity_name}"
)


class ProactiveService:
    """活动驱动的主动行为服务（v4.4 新增）。"""

    # 活动开始后 ≤ 此分钟内才考虑触发主动发起（避免半天后突然冒一句）
    PROACTIVE_FRESH_WINDOW_MINUTES = 5
    # v4.4.1：proactive_streams 解析缓存有效期
    RESOLVE_CACHE_TTL_SUCCESS = 600.0  # 解析成功缓存 10 分钟
    RESOLVE_CACHE_TTL_FAILURE = 60.0   # 解析失败缓存 60 秒（短期避免反复查 + 允许群恢复重试）

    def __init__(self, engine: "ScheduleEngine") -> None:
        """初始化 ProactiveService。

        Args:
            engine: 当前插件拥有的日程引擎
        """
        self._engine = engine
        self._plugin = engine.plugin
        self._patrol_lock = asyncio.Lock()
        self._stop_event: asyncio.Event = asyncio.Event()
        # 时区管理器（与 InjectService 用同一个时区）
        self._tz_manager: TimezoneManager = engine.timezone_manager
        # 已触发的主动发起记录：{(stream_id, goal_id, date_str): trigger_time}
        # 同一个活动在同一天同一个 stream 只主动触发一次
        self._proactive_history: Dict[Tuple[str, str, str], float] = {}
        # 已应用的频率因子缓存：{stream_id: factor}（避免重复 set_adjust）
        self._current_factor: Dict[str, float] = {}
        # v4.4.1 新增：proactive_streams 解析缓存
        # 结构：{raw_entry: (session_id 或 None, expire_at)}
        # 解析成功缓存 10 分钟；失败缓存 60 秒（短期避免反复查 + 允许群恢复重试）
        self._resolved_cache: Dict[str, Tuple[Optional[str], float]] = {}
        self._life_state: Dict[str, Any] = {}
        self._managed_private_streams: Set[str] = set()
        self._activity_task: Optional[asyncio.Task] = None
        self._activity_attempts: Dict[Tuple[str, str, str], float] = {}
        self._activity_state: Dict[str, Any] = {}
        self._activity_state_loaded = False
        self._state_lock = asyncio.Lock()
        logger.debug("ProactiveService 初始化（v4.4）")

    async def stop(self) -> None:
        """停止巡检并恢复本服务施加的聊天频率。"""
        self._stop_event.set()
        if self._activity_task is not None:
            self._activity_task.cancel()
            await asyncio.gather(self._activity_task, return_exceptions=True)
            self._activity_task = None
        async with self._patrol_lock:
            if self._current_factor:
                await self._apply_frequency(list(self._current_factor), "daily_routine")

    async def patrol(self) -> None:
        """由 MaiTrace 统一周期调用，不另建主动巡检循环。"""
        if self._stop_event.is_set():
            return
        async with self._patrol_lock:
            if self._stop_event.is_set():
                return
            await self._check_and_act()

    def update_life_state(self, state: Dict[str, Any], *, managed_private_streams: Optional[Set[str]] = None) -> None:
        """统一生活周期提前提供公开快照，主动任务不在 Hook 中读库或请求模型。"""
        self._life_state = dict(state)
        self._managed_private_streams = set(managed_private_streams or ())

    async def _load_state(self) -> None:
        """冷却、每日额度和成功约定在重载后继续有效；损坏记录明确报错。"""
        if self._activity_state_loaded:
            return
        path = self._engine.data_dir / "proactive_state.json"

        def read() -> Dict[str, Any]:
            try:
                value = json.loads(path.read_text(encoding="utf-8"))
            except FileNotFoundError:
                return {"streams": {}, "attempts": [], "commitments": []}
            if not isinstance(value, dict) or not isinstance(value.get("streams"), dict):
                raise ValueError("proactive_state.json 格式错误")
            for field in ("attempts", "commitments"):
                if not isinstance(value.get(field), list):
                    raise ValueError(f"proactive_state.json 缺少有效的 {field}")
            return value

        async with self._state_lock:
            if self._activity_state_loaded:
                return
            value = await asyncio.to_thread(read)
            cutoff = self._tz_manager.get_now().timestamp() - 172800
            for field, target in (("attempts", self._activity_attempts), ("commitments", self._proactive_history)):
                for item in value[field]:
                    key = (str(item["stream_id"]), str(item["goal_id"]), str(item["day"]))
                    at = float(item["at"])
                    if at >= cutoff:
                        target[key] = at
            self._activity_state = value["streams"]
            self._activity_state_loaded = True

    async def _save_state(self) -> None:
        """原子保存；取消时等写入线程结束，再允许旧实例关闭或新实例接管。"""
        async with self._state_lock:
            cutoff = self._tz_manager.get_now().timestamp() - 172800
            self._activity_attempts = {key: at for key, at in self._activity_attempts.items() if at >= cutoff}
            self._proactive_history = {key: at for key, at in self._proactive_history.items() if at >= cutoff}

            def entries(mapping: Dict[Tuple[str, str, str], float]) -> List[Dict[str, Any]]:
                return [{"stream_id": key[0], "goal_id": key[1], "day": key[2], "at": at}
                        for key, at in mapping.items()]

            payload = {"version": 1, "streams": dict(self._activity_state),
                       "attempts": entries(self._activity_attempts), "commitments": entries(self._proactive_history)}
            task = asyncio.create_task(asyncio.to_thread(write_json, self._engine.data_dir / "proactive_state.json", payload))
            try:
                await asyncio.shield(task)
            except asyncio.CancelledError:
                await task
                raise

    def _activity_available(self, name: str, goal_type: str, params: Dict[str, Any], now: datetime) -> bool:
        """先按事实排除不宜主动聊天的状态，再请求模型判断是否值得分享。"""
        cfg = self._plugin.config.schedule
        if (self._stop_event.is_set() or not cfg.enabled or not cfg.activity_proactive_enabled
                or not cfg.activity_proactive_daily_max or params.get("is_commitment")):
            return False
        start, end, current = cfg.activity_proactive_quiet_start, cfg.activity_proactive_quiet_end, now.strftime("%H:%M")
        quiet = start != end and (start <= current < end if start < end else current >= start or current < end)
        if quiet:
            return False
        activity_type = _classify_activity(goal_type, name + " " + str(params.get("activity_description", "")))
        if activity_type in {ActivityType.SLEEPING, ActivityType.WORKING, ActivityType.STUDYING,
                             ActivityType.COMMUTING, ActivityType.EXERCISING, ActivityType.SELF_CARE,
                             ActivityType.BATHING}:
            return False
        life = self._life_state
        if life.get("enabled") and (float(life["energy"]) < 30 or life.get("sleep_phase") in {
            "falling_asleep", "light_sleep", "deep_sleep",
        }):
            return False
        current_activity = self._engine.peek()
        return bool(current_activity is not None and current_activity.current is not None
                    and current_activity.current.goal_id == params.get("activity_goal_id")
                    and current_activity.current.start_at <= now < current_activity.current.end_at)

    def _stream_available(self, stream_id: str, key: Tuple[str, str, str], now: datetime) -> bool:
        if key in self._activity_attempts or stream_id in self._managed_private_streams:
            return False
        return self._within_stream_limits(stream_id, now)

    def _within_stream_limits(self, stream_id: str, now: datetime) -> bool:
        """模型等待期间可以修改冷却或额度，提交前仍执行最新限制。"""
        cfg = self._plugin.config.schedule
        previous = self._activity_state.get(stream_id, {})
        count = int(previous.get("count", 0)) if previous.get("day") == now.date().isoformat() else 0
        return (count < cfg.activity_proactive_daily_max
                and now.timestamp() - float(previous.get("last_at", 0)) >= cfg.activity_proactive_cooldown_minutes * 60)

    async def _consider_activity(self, streams: List[str], name: str, goal_type: str, params: Dict[str, Any]) -> None:
        """普通日程只提供自然话题；模型和 Planner 都可以决定沉默。"""
        try:
            now = self._tz_manager.get_now()
            if not self._activity_available(name, goal_type, params, now):
                return
            await self._load_state()
            keys = [(stream, str(params["activity_goal_id"]), now.date().isoformat()) for stream in streams]
            eligible = [key for key in keys if self._stream_available(key[0], key, now)]
            if not eligible:
                return
            # 活动内只判断一次，失败/拒绝也记录，避免每 30 秒重问模型；下个活动可再判断。
            for key in eligible:
                self._activity_attempts[key] = now.timestamp()
            await self._save_state()
            if not self._engine.bot_profile:
                await self._engine.prepare_generation()
            context = {"time": now.isoformat(timespec="minutes"), "activity": name,
                       "description": params.get("activity_description", ""),
                       "start": params["activity_start_at"], "end": params["activity_end_at"],
                       "persona": self._engine.bot_profile,
                       "life": {key: self._life_state[key] for key in ("energy", "hunger", "mood_valence", "scene", "current_location")
                                if self._life_state.get("enabled") and key in self._life_state}}
            prompt = (
                "判断此刻是否有自然的生活话题值得主动与熟悉的聊天对象分享。"
                "忙碌、疲惫、没有具体想法时 should_speak=false。不要逐项通知日程切换，"
                "不要把计划写成完成、编造经历、天气或他人的行为。符合人设，情绪依照实际状态，"
                "一两个具体小细节即可。只返回 JSON 对象：should_speak（布尔）、topic（短话题）、motive（分享动机）。\n"
                + wrap_untrusted(json.dumps(context, ensure_ascii=False), label="当前活动与生活状态", max_chars=3500)
            )
            cfg = self._plugin.config.schedule
            runner = LLMRunner(self._plugin.ctx, cfg.llm_task_name, timeout=min(60, int(cfg.generation_timeout)))
            success, response = await runner.generate(prompt, temperature=0.4, max_tokens=500)
            if not success:
                raise RuntimeError(response)
            decision = json_object(response)
            if not isinstance(decision.get("should_speak"), bool):
                raise ValueError("主动判断缺少有效的 should_speak 布尔值")
            if not decision["should_speak"]:
                return
            topic = required_text(decision.get("topic"), "topic", 220)
            motive = required_text(decision.get("motive"), "motive", 220)
            # 生成期间可能已跨日、切换活动、降低精力或修改白名单，提交前重新检查。
            now = self._tz_manager.get_now()
            if not self._activity_available(name, goal_type, params, now):
                return
            allowed = await self._resolve_proactive_streams(self._plugin.config.schedule.proactive_streams)
            for key in eligible:
                now = self._tz_manager.get_now()
                if (key[0] not in allowed or key[0] in self._managed_private_streams
                        or key[2] != now.date().isoformat() or not self._activity_available(name, goal_type, params, now)
                        or not self._within_stream_limits(key[0], now)):
                    continue
                reason = "此刻的生活素材，仅供 Planner 判断是否自然开口；无需一定发送，不公告作息。\n" + wrap_untrusted(
                    json.dumps({"activity": name, "topic": topic, "motive": motive}, ensure_ascii=False),
                    label="主动聊天话题", max_chars=1000)
                result = await self._plugin.ctx.maisaka.trigger_proactive(
                    stream_id=key[0], intent="maitrace_schedule", reason=reason, priority="low",
                    metadata={"maitrace_source": "schedule", "maitrace_activity_id": params["activity_goal_id"]},
                )
                if not isinstance(result, dict) or result.get("success") is not True or not result.get("task_id"):
                    logger.warning("普通日程主动任务未获确认: stream=%s, result=%r", key[0], result)
                    continue
                previous = self._activity_state.get(key[0], {})
                count = int(previous.get("count", 0)) if previous.get("day") == key[2] else 0
                self._activity_state[key[0]] = {"day": key[2], "count": count + 1, "last_at": now.timestamp()}
                await self._save_state()
                logger.info("普通日程主动任务已提交: activity=%s, stream=%s", name, key[0])
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("普通日程主动判断失败")

    # ------------------------------------------------------------
    # 主循环逻辑
    # ------------------------------------------------------------

    async def _check_and_act(self) -> None:
        """读当前活动 → 决定是否触发主动发起 / 调整频率。"""
        cfg = self._plugin.config.schedule
        raw_streams = cfg.proactive_streams
        if not cfg.enabled or not raw_streams:
            # 日程关闭或白名单为空时恢复已有频率，并停止主动行为。
            if self._current_factor:
                await self._apply_frequency(list(self._current_factor), "daily_routine")
            return

        # v4.4.1：把 qq:group:xxx / qq:private:xxx 等格式解析为真实 session_id
        proactive_streams = await self._resolve_proactive_streams(raw_streams)
        removed_streams = [stream_id for stream_id in self._current_factor if stream_id not in proactive_streams]
        if removed_streams:
            await self._apply_frequency(removed_streams, "daily_routine")
        if not proactive_streams:
            # 所有条目都解析失败，恢复旧频率后跳过本轮。
            return

        await self._load_state()

        # 找当前正在进行的活动
        activity_info = await self._find_current_activity()
        if activity_info is None:
            if self._current_factor:
                await self._apply_frequency(list(self._current_factor), "daily_routine")
            return
        activity_name, goal_type, start_minutes, _end_minutes, activity_params = activity_info

        now = self._tz_manager.get_now()
        current_min = now.hour * 60 + now.minute
        date_str = now.strftime("%Y-%m-%d")

        # 1. 频率调控（每个 stream 独立）
        if cfg.enable_frequency_modulation:
            activity_type = _classify_activity(goal_type, activity_name + " " + str(activity_params.get("activity_description", "")))
            await self._apply_frequency(proactive_streams, activity_type.value)
        elif self._current_factor:
            await self._apply_frequency(list(self._current_factor), "daily_routine")

        # 普通活动由后台模型判断是否有自然话题；生活协调周期立即继续推进。
        if not activity_params.get("is_commitment"):
            if cfg.activity_proactive_enabled and (self._activity_task is None or self._activity_task.done()):
                self._activity_task = asyncio.create_task(
                    self._consider_activity(proactive_streams, activity_name, goal_type, activity_params),
                    name="maitrace-activity-proactive",
                )
            return
        # 2. 履行已确认约定，在约定开始的前 5 分钟内尝试
        if not cfg.enable_proactive_trigger:
            return
        if not 0 <= current_min - start_minutes <= self.PROACTIVE_FRESH_WINDOW_MINUTES:
            return  # 错过新鲜窗口

        # 约定只归提出请求的会话；旧版未保存目标的约定保留作息用途，不广播。
        target_stream_id = str(activity_params.get("commitment_stream_id", ""))
        for stream_id in proactive_streams:
            if stream_id != target_stream_id:
                continue
            activity_key = str(activity_params.get("activity_goal_id") or activity_name)
            key = (stream_id, activity_key, date_str)
            if key in self._proactive_history:
                continue  # 同一天同一活动只触发一次
            if await self._trigger_proactive(stream_id, activity_name, goal_type, activity_params):
                self._proactive_history[key] = now.timestamp()
                await self._save_state()

    # ------------------------------------------------------------
    # v4.4.1 新增：proactive_streams 格式解析
    # ------------------------------------------------------------

    async def _resolve_proactive_streams(self, raw_entries: List[str]) -> List[str]:
        """把 ``proactive_streams`` 配置项解析为真实 session_id 列表。

        支持三种格式（与 ``allowed_streams`` 一致）：
            - ``session:<id>``       → 直接取 ``<id>``
            - ``qq:group:<gid>``     → ``ctx.chat.get_stream_by_group_id`` 解析
            - ``qq:private:<uid>``   → ``ctx.chat.get_stream_by_user_id`` 解析
            - ``<id>``               → 当作 session_id 直接用（向后兼容）

        遵守项目会话 ID 规范：**不自行计算 session_id**，解析失败的条目直接
        跳过 + warn，不写入任何 fallback hash。

        Args:
            raw_entries: 配置文件中原始的 ``proactive_streams`` 列表。

        Returns:
            解析成功的 session_id 列表（按输入顺序，去重）。
        """
        resolved: List[str] = []
        seen: Set[str] = set()
        now = time.time()
        ctx_obj = self._plugin.ctx

        for raw in raw_entries:
            entry = str(raw).strip()
            if not entry:
                continue

            # 命中缓存（含失败缓存）
            cached = self._resolved_cache.get(entry)
            if cached is not None and now < cached[1]:
                resolved_sid = cached[0]
                if resolved_sid and resolved_sid not in seen:
                    resolved.append(resolved_sid)
                    seen.add(resolved_sid)
                continue

            # 解析逻辑：按格式分支
            session_id: Optional[str] = None
            if entry.startswith("session:"):
                # session:<id> → 直接取
                session_id = entry[len("session:"):].strip() or None
            elif ":group:" in entry:
                # qq:group:<gid> → 用 chat_manager 解析
                session_id = await self._resolve_via_chat_capability(
                    entry, kind="group", ctx_obj=ctx_obj,
                )
            elif ":private:" in entry:
                # qq:private:<uid> → 用 chat_manager 解析
                session_id = await self._resolve_via_chat_capability(
                    entry, kind="private", ctx_obj=ctx_obj,
                )
            else:
                # 兜底：纯字符串直接当 session_id（向后兼容旧配置）
                session_id = entry

            # 写缓存
            ttl = self.RESOLVE_CACHE_TTL_SUCCESS if session_id else self.RESOLVE_CACHE_TTL_FAILURE
            self._resolved_cache[entry] = (session_id, now + ttl)

            if session_id and session_id not in seen:
                resolved.append(session_id)
                seen.add(session_id)

        return resolved

    async def _resolve_via_chat_capability(
        self,
        entry: str,
        kind: str,
        ctx_obj: Any,
    ) -> Optional[str]:
        """通过 ``ctx.chat`` 把 ``qq:group:<gid>`` / ``qq:private:<uid>`` 解析为 session_id。

        Args:
            entry: 原始配置条目（如 ``"qq:group:123456"``）。
            kind: ``"group"`` 或 ``"private"``。
            ctx_obj: 当前插件的 SDK 上下文。

        Returns:
            真实 session_id；解析失败返回 ``None`` 并 warn。
        """
        parts = entry.split(":", 2)
        if len(parts) != 3:
            logger.warning(f"配置条目格式错误：{entry}（应为 <platform>:{kind}:<id>）")
            return None
        platform, _, target_id = parts
        platform = platform.strip() or "qq"
        target_id = target_id.strip()
        if not target_id:
            logger.warning(f"配置条目缺失 ID：{entry}")
            return None

        try:
            if kind == "group":
                result = await ctx_obj.chat.get_stream_by_group_id(target_id, platform)
            else:
                result = await ctx_obj.chat.get_stream_by_user_id(target_id, platform)
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"解析 {entry} 异常: {exc}")
            return None

        # SDK 的 _normalize_capability_result 对 chat.get_stream_by_xxx 自动解包：
        # - Host 原始 `{"success": True, "stream": <dict>}` 被剥出 → 直接拿到 stream dict
        # - Host 找不到对应聊天流时 stream 字段为 None → SDK 透传 None 给我们
        # - 真正失败（capability_denied 等）返回原始 `{"success": False, "error": "..."}`
        #   （没有 stream key 时不被剥包）
        # 所以判断顺序必须是：先看是否含 success=False，再看是否 None，最后才取 session_id
        if isinstance(result, dict) and result.get("success") is False:
            logger.warning(f"解析 {entry} 失败: {result.get('error', '主程序拒绝')}")
            return None
        if result is None:
            logger.warning(f"解析 {entry} 失败：主程序未找到对应聊天流（可能 bot 与该 QQ 未建立过私聊 / 已退群）")
            return None
        if not isinstance(result, dict):
            logger.warning(f"解析 {entry} 失败：SDK 返回类型异常 {type(result).__name__}={result!r}")
            return None
        session_id = str(result.get("session_id") or "").strip()
        if not session_id:
            logger.warning(f"解析 {entry} 失败：返回 stream 无 session_id 字段（result={result!r}）")
            return None
        logger.info(f"解析成功 {entry} → session_id={session_id}")
        return session_id

    async def _find_current_activity(
        self,
    ) -> Optional[Tuple[str, str, int, int, Dict[str, Any]]]:
        """从共享内存快照读取当前活动，使用查询时确认的实际起止时间。

        Returns:
            ``(name, goal_type, start_minutes, end_minutes, parameters)`` —— 多返回的
            ``parameters`` dict 透传 goal.parameters 原始内容，供下游识别
            ``is_commitment`` / ``commitment_*`` 等 v4.4.5 新增字段。
            没找到时为 ``None``。
        """
        snapshot = self._engine.peek()
        if snapshot is None or snapshot.current is None:
            return None
        current = snapshot.current
        if current.start_at is None or current.end_at is None:
            raise RuntimeError("当前活动快照缺少实际起止时间")
        now = self._tz_manager.get_now()
        midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
        parameters = dict(current.parameters)
        parameters["activity_goal_id"] = current.goal_id
        parameters["activity_description"] = current.description
        parameters["activity_start_at"] = current.start_at.isoformat()
        parameters["activity_end_at"] = current.end_at.isoformat()
        return (
            current.name, current.goal_type,
            int((current.start_at - midnight).total_seconds() // 60),
            int((current.end_at - midnight).total_seconds() // 60),
            parameters,
        )

    # ------------------------------------------------------------
    # 频率调控
    # ------------------------------------------------------------

    async def _apply_frequency(self, streams: List[str], goal_type: str) -> None:
        """按 goal_type 映射频率因子并对每个 stream 应用。

        Args:
            streams: 目标聊天流 ID 列表
            goal_type: 当前活动类型
        """
        target_factor = _FREQUENCY_FACTOR_BY_GOAL_TYPE.get(goal_type, 1.0)
        ctx_obj = self._plugin.ctx
        for stream_id in streams:
            if target_factor != 1.0 and (
                self._stop_event.is_set() or not self._plugin.config.schedule.enabled
            ):
                return
            # 同一 stream 因子不变则跳过，避免反复 set_adjust 刷日志
            if self._current_factor.get(stream_id) == target_factor:
                continue
            try:
                result = await ctx_obj.frequency.set_adjust(stream_id, target_factor)
                # SDK 已把 Host 载荷解包为 bool；只有明确的 True 才能记录成功。
                if result is not True:
                    logger.warning(
                        f"频率调控失败: stream={stream_id}, factor={target_factor}, "
                        f"reason=主程序未确认成功（返回 {result!r}）"
                    )
                    continue
                self._current_factor[stream_id] = target_factor
                logger.info(
                    f"🎚️ 频率调控: stream={stream_id}, "
                    f"goal_type={goal_type}, factor={target_factor}"
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning(f"频率调控异常: stream={stream_id}, {exc}")

    # ------------------------------------------------------------
    # 主动发起
    # ------------------------------------------------------------

    async def _trigger_proactive(
        self,
        stream_id: str,
        activity_name: str,
        goal_type: str,
        activity_params: Dict[str, Any],
    ) -> bool:
        """对单个聊天流触发一次主动开口。

        Args:
            stream_id: 目标聊天流 ID
            activity_name: 当前活动名（用于注入 intent prompt）
            goal_type: 当前活动类型（供日志记录）
            activity_params: 当前活动的 goal.parameters；仅接受已确认约定，
                普通作息切换不产生主动通知。
        """
        cfg = self._plugin.config.schedule
        if self._stop_event.is_set() or not cfg.enabled or not cfg.enable_proactive_trigger:
            return False
        ctx_obj = self._plugin.ctx
        # v4.4.5：约定来源走强指令模板
        is_commitment = bool(activity_params.get("is_commitment"))
        if not is_commitment or activity_params.get("commitment_stream_id") != stream_id:
            return False
        commit_title = str(activity_params.get("commitment_title") or activity_name).strip()
        commit_time = str(activity_params.get("commitment_time") or "").strip()
        commit_notes = str(activity_params.get("commitment_notes") or "").strip()
        intent_prompt = _COMMITMENT_INTENT_TEMPLATE.format(
            time_hint=f"{commit_time} " if commit_time else "",
            title=commit_title,
            notes_hint=commit_notes or "无",
            activity_name=activity_name,
        )
        reason = f"MaiTrace: 约定到期 → {commit_title}"

        try:
            result = await ctx_obj.maisaka.trigger_proactive(
                stream_id=stream_id,
                intent=intent_prompt,
                reason=reason,
                priority="high",
            )
            if not isinstance(result, dict) or result.get("success") is not True or not result.get("task_id"):
                logger.warning(
                    f"主动发起被拒: stream={stream_id}, activity={activity_name}, "
                    f"reason=主程序未确认任务 {result!r}"
                )
                return False
            logger.info(
                f"🚀 主动发起触发: stream={stream_id}, activity={activity_name}, "
                f"goal_type={goal_type}, commitment={is_commitment}"
            )
            return True
        except Exception as exc:  # noqa: BLE001
            logger.warning(f"主动发起异常: stream={stream_id}, {exc}")
            return False
