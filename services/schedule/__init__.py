"""日程引擎（吸收自 xuqian13.autonomous-planning-plugin-v4 v4.4.5）。

## 目录来源

``planner/`` ``database/`` ``core/`` ``cache/`` ``utils/`` 五棵子树以规划插件为基础，
内部相对导入（``..core.exceptions`` / ``...utils.time_utils`` 等）全部闭合在本包内，
合并时补齐实例归属、异步数据库调度与明确的 RPC 超时。

``utils/`` 是本包**私有**的时间/时区/能量工具，和 MaiTrace 顶层的 ``MaiTrace/utils/``
是两个不同的包，不要混淆。

## 本包新写的部分

- ``query.py``：从 planning-v4 的 ``InjectService._get_current_schedule`` 抽出来的纯查询
- ``snapshot.py``：hook 专用的内存快照（6 秒预算的关键，见该模块 docstring）
- ``ScheduleEngine``（本文件）：持有 GoalManager / 生成器 / 快照的所有权容器

## 为什么要有 ScheduleEngine

planning-v4 的 ``planner/goal_manager.py`` 末尾有一个模块级单例::

    _goal_manager: Optional[GoalManager] = None
    def get_goal_manager() -> GoalManager: ...
    def close_goal_manager() -> None: ...

在插件 Runner 进程里，模块级单例意味着：数据目录在首次调用时被固化、多插件实例
共享同一个连接、卸载后残留。MaiTrace 改为**由插件实例持有**——``ScheduleEngine``
在 ``on_load`` 创建、``on_unload`` 关闭，路径显式传入，不依赖任何全局状态。

原模块级管理器已经移除；所有后台任务、工具与命令均访问本实例的管理器。
"""

from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List, Optional, Set, Tuple
import asyncio
import time

from ...utils import get_global_str, get_logger
from .database.worker import DatabaseWorker
from .query import CurrentActivity, ScheduleQueryResult, query_current_schedule
from .snapshot import ActivitySnapshot, ScheduleSnapshotCache

logger = get_logger(__name__)

__all__ = [
    "ScheduleEngine",
    "ActivitySnapshot",
    "ScheduleSnapshotCache",
    "CurrentActivity",
    "ScheduleQueryResult",
    "query_current_schedule",
]

_DB_FILENAME = "goals.db"
# 人格配置、历史和知识库分别预留 30 / 45 / 30 秒，合计不超过组件的固定余量。
PREPARE_GENERATION_TIMEOUT_SECONDS = 30.0


class ScheduleEngine:
    """日程引擎的所有权容器。

    把 ``GoalManager`` / ``ScheduleGenerator`` / ``ScheduleSnapshotCache`` 的生命周期
    收敛到一个对象上，由 ``MaiTracePlugin`` 在 ``on_load`` / ``on_unload`` 里管理，
    替代 planning-v4 的模块级单例。

    SQLite 的创建、查询和关闭都由同一专用线程串行执行；hook 只通过 ``peek``
    读取内存，不阻塞事件循环。
    """

    def __init__(self, plugin: Any, *, data_dir: Path) -> None:
        """
        Args:
            plugin: MaiTracePlugin 实例。生成器通过共用的 ``LLMRunner``
                访问 SDK，并显式传递日程生成超时。
            data_dir: 数据目录，``goals.db`` 落在这里。
        """
        self._plugin = plugin
        self._data_dir = Path(data_dir)
        self._goal_manager: Optional[Any] = None
        self._timezone_manager: Optional[Any] = None
        self._snapshot: Optional[ScheduleSnapshotCache] = None
        self._started = False
        self._worker: Optional[DatabaseWorker] = None
        self._scheduler: Optional[Any] = None
        self._tools: Optional[Any] = None
        self._proactive: Optional[Any] = None
        self._refresh_task: Optional[asyncio.Task] = None
        self._operations: Set[asyncio.Task] = set()
        self._lifecycle_lock = asyncio.Lock()
        self.mutation_lock = asyncio.Lock()
        self._bot_profile: Dict[str, Any] = {}
        self._life_state: Dict[str, Any] = {}
        self._managed_private_streams: Set[str] = set()

    # ---------- 生命周期 ----------

    async def start(self) -> None:
        """建库、建 GoalManager 和快照缓存，并做一次初始刷新。"""
        async with self._lifecycle_lock:
            if self._started or not self._schedule_config().enabled:
                return
            try:
                # 线程内的构造不能被 asyncio 取消。等实例归属确定后再传播取消，
                # 保证已创建的 SQLite 连接一定进入下面的关闭流程。
                await self._finish_owned_task(asyncio.create_task(self._start_resources()))
            except BaseException:
                await self._finish_owned_task(asyncio.create_task(self._stop_resources(asyncio.current_task())))
                raise

    async def _start_resources(self) -> None:
        """在受保护的生命周期任务中取得数据库及后台任务的所有权。"""
        # 延迟导入：日程禁用时不初始化数据库或后台调度。
        from .planner.auto_scheduler import ScheduleAutoScheduler
        from .planner.goal_manager import GoalManager
        from .proactive import ProactiveService
        from .tools import ToolsService
        from .utils.timezone_manager import TimezoneManager

        timezone = self._config_timezone()
        self._timezone_manager = TimezoneManager(timezone)
        self._worker = DatabaseWorker()
        # 连接的创建和关闭都进入同一专用线程，避免换线程留下连接。
        self._goal_manager = await self.run_db(
            GoalManager, data_dir=str(self._data_dir), db_name=_DB_FILENAME,
            timezone=timezone,
        )
        self._snapshot = ScheduleSnapshotCache(
            self._goal_manager, self._timezone_manager,
            ttl_seconds=self._config_cache_ttl(),
            cross_day_activity=self._config_cross_day(),
            run_db=self.run_db,
        )
        self._tools = ToolsService(self)
        self._proactive = ProactiveService(self)
        self._proactive.update_life_state(self._life_state, managed_private_streams=self._managed_private_streams)
        await self._snapshot.refresh(force=True)
        self._started = True
        self._scheduler = ScheduleAutoScheduler(self._plugin, engine=self)
        await self._scheduler.start()
        self._refresh_task = asyncio.create_task(self._background_loop())
        logger.info("日程引擎已启动 (db=%s, tz=%s)", self._data_dir / _DB_FILENAME, timezone)

    @staticmethod
    async def _finish_owned_task(task: asyncio.Task) -> None:
        """取消调用方时，先等资源创建或释放完成，再向调用方传播取消。"""
        cancelled = False
        while True:
            try:
                await asyncio.shield(task)
                break
            except asyncio.CancelledError:
                if task.cancelled():
                    raise
                cancelled = True
        if cancelled:
            raise asyncio.CancelledError

    async def stop(self) -> None:
        """取消在途操作、恢复聊天频率并关闭数据库连接。"""
        async with self._lifecycle_lock:
            if self._worker is None:
                return
            self._started = False
            await self._finish_owned_task(asyncio.create_task(self._stop_resources(asyncio.current_task())))

    async def _stop_resources(self, caller: Optional[asyncio.Task]) -> None:
        """停止所有使用数据库的任务，即使其中一步失败也关闭底层连接。"""
        self._started = False
        pending = list(self._operations)
        if self._refresh_task is not None:
            pending.append(self._refresh_task)
        pending = [task for task in pending if task not in {caller, asyncio.current_task()}]
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        self._operations.clear()
        self._refresh_task = None
        try:
            if self._scheduler is not None:
                await self._scheduler.stop()
        finally:
            self._scheduler = None
            try:
                if self._proactive is not None:
                    await self._proactive.stop()
            finally:
                self._proactive = None
                try:
                    if self._worker is not None:
                        close = self._goal_manager.close if self._goal_manager is not None else None
                        await self._worker.close(close)
                finally:
                    self._worker = None
                    self._goal_manager = None
                    self._timezone_manager = None
                    self._snapshot = None
                    self._tools = None
        logger.info("日程引擎已停止")

    @contextmanager
    def _track_operation(self) -> Iterator[None]:
        """记录最外层操作；命令嵌套调用工具时不提前移除同一个任务。"""
        task = asyncio.current_task()
        added = task is not None and task not in self._operations
        if added:
            self._operations.add(task)
        try:
            yield
        finally:
            if added:
                self._operations.discard(task)

    async def reload_config(self) -> None:
        """重建时区、调度时间和缓存；取消旧任务后复用本插件数据库。"""
        await self.stop()
        await self.start()

    @property
    def plugin(self) -> Any:
        return self._plugin

    @property
    def data_dir(self) -> Path:
        return self._data_dir

    @property
    def bot_profile(self) -> Dict[str, Any]:
        return dict(self._bot_profile)

    def update_life_state(self, state: Dict[str, Any], *, managed_private_streams: Optional[Set[str]] = None) -> None:
        """生活快照用于生成和主动判断；日程工具、Hook 无需自行推进状态。"""
        from copy import deepcopy

        self._life_state = deepcopy(state)
        self._managed_private_streams = set(managed_private_streams or ())
        if self._proactive is not None:
            self._proactive.update_life_state(self._life_state, managed_private_streams=self._managed_private_streams)

    @property
    def timezone_manager(self) -> Any:
        if self._timezone_manager is None:
            raise RuntimeError("日程引擎尚未启动")
        return self._timezone_manager

    async def run_db(self, function: Callable[..., Any], *args: Any, **kwargs: Any) -> Any:
        """唯一 SQLite 调度入口，初始化阶段也可使用。"""
        if self._worker is None:
            raise RuntimeError("日程数据库尚未启动或已关闭")
        return await self._worker.call(function, *args, **kwargs)

    async def prepare_generation(self) -> None:
        """在统一截止时间内取得人格配置，成功后一次性替换生成上下文。"""
        from ..persona import resolve_persona

        deadline = asyncio.timeout(PREPARE_GENERATION_TIMEOUT_SECONDS)
        try:
            async with deadline:
                persona = await resolve_persona(self._plugin)
                self._bot_profile = {
                    "nickname": persona.nickname,
                    "bot_name": persona.nickname,
                    "alias_names": list(persona.alias_names),
                    "personality": persona.personality,
                    "reply_style": persona.style,
                    "self_description": persona.self_description,
                    "interest": await get_global_str(self._plugin.ctx, "personality.interest", ""),
                }
        except TimeoutError as exc:
            if deadline.expired():
                raise TimeoutError(
                    f"日程人格配置读取超过整体截止时间（{PREPARE_GENERATION_TIMEOUT_SECONDS:g} 秒）"
                ) from exc
            raise

    async def handle_tool(self, tool_name: str, **kwargs: Any) -> Dict[str, Any]:
        """执行作息查询、生成和临时约定工具，管理器属于当前插件实例。"""
        if not self._started or not self._schedule_config().enabled or self._tools is None:
            return {"type": "error", "content": "日程引擎未启用或尚未就绪"}
        dispatch = {
            "get_planning_status": self._tools.get_planning_status,
            "generate_schedule": self._tools.generate_schedule,
            "update_schedule": self._tools.update_schedule,
        }
        if tool_name not in dispatch:
            return {"type": "error", "content": f"未知日程工具: {tool_name}"}
        from .utils.stream_filter import is_stream_allowed

        stream_id = str(kwargs.get("stream_id") or kwargs.get("chat_id") or "")
        stream_info = kwargs.get("stream_info")
        if not isinstance(stream_info, dict):
            stream_info = {key: kwargs.get(key, "") for key in ("platform", "group_id", "user_id")}
        if stream_id and not is_stream_allowed(stream_id, self._schedule_config().allowed_streams, stream_info):
            return {"type": "error", "content": "当前聊天未启用日程功能"}
        with self._track_operation():
            async with self.mutation_lock:
                result = await dispatch[tool_name](**kwargs)
                await self.refresh(force=True)
                return result

    async def handle_command(
        self, content: str, stream_id: str = "", user_id: str = "",
        platform: str = "qq", **kwargs: Any,
    ) -> Tuple[bool, str, bool]:
        """/zn plan 的唯一分发入口，支持完整命令及子命令文本。"""
        from ...handlers.planning import PlanningHandler

        if not self._started or not self._schedule_config().enabled:
            return False, "日程引擎未启用或尚未就绪", True
        with self._track_operation():
            return await PlanningHandler(self).execute(
                content, stream_id=stream_id, user_id=user_id, platform=platform, **kwargs
            )

    async def patrol_proactive(self) -> None:
        """统一周期调用的活动切换、约定提醒和频率调节入口。"""
        if not self._started or self._proactive is None:
            return
        with self._track_operation():
            if self._schedule_config().enabled:
                await self.refresh(force=True)
            await self._proactive.patrol()

    async def cleanup(self) -> Dict[str, int]:
        """完成到期日程并按保留期清理旧目标、生成日志。"""
        from .utils.llm_logger import cleanup_old_logs

        cfg = self._plugin.config.goal_cleanup
        expired = await self.run_db(self.goal_manager.cleanup_expired_schedules)
        removed = await self.run_db(self.goal_manager.cleanup_old_goals, days=cfg.cleanup_old_goals_days)
        log_count = await asyncio.to_thread(
            cleanup_old_logs, self._data_dir / "llm_logs",
            self._schedule_config().llm_log_retention_days,
        )
        return {"expired": expired, "removed": removed, "logs": log_count}

    async def _background_loop(self) -> None:
        """维护内存快照与清理任务；hook 无需等待 SQLite。"""
        last_cleanup = 0.0
        while self._started:
            try:
                if time.monotonic() - last_cleanup >= self._plugin.config.goal_cleanup.cleanup_interval:
                    await self.cleanup()
                    last_cleanup = time.monotonic()
                await self.refresh()
            except Exception:
                logger.exception("日程后台刷新或清理失败")
            await asyncio.sleep(min(60.0, self._config_cache_ttl()))

    async def activity_segments(self, start: datetime, end: datetime) -> List[Dict[str, Any]]:
        """返回实际保存的日程区间，供生活状态最多回放 72 小时；不补造缺失日程。"""
        from .planner.goal_manager import GoalStatus
        from .query import _extract_time_window
        from .utils.time_utils import parse_time_window

        tz = self.timezone_manager.get_now().tzinfo
        start = start.replace(tzinfo=tz) if start.tzinfo is None else start.astimezone(tz)
        end = end.replace(tzinfo=tz) if end.tzinfo is None else end.astimezone(tz)
        if end <= start:
            return []
        start = max(start, end - timedelta(hours=72))
        goals = await self.run_db(self.goal_manager.get_all_goals, chat_id="global")
        segments: List[Dict[str, Any]] = []
        for goal in goals:
            if goal.status not in {GoalStatus.ACTIVE, GoalStatus.COMPLETED}:
                continue
            if goal.goal_type == "pending_commitment" or goal.created_at is None:
                continue
            window = _extract_time_window(goal)
            if window is None:
                continue
            begin_min, end_min = parse_time_window(window)
            if begin_min is None or end_min is None:
                continue
            day = goal.created_at.astimezone(tz) if goal.created_at.tzinfo is not None else goal.created_at.replace(tzinfo=tz)
            day = day.replace(hour=0, minute=0, second=0, microsecond=0)
            begin_at, end_at = day + timedelta(minutes=begin_min), day + timedelta(minutes=end_min)
            if begin_at < end and end_at > start:
                segments.append({
                    "start": begin_at, "end": end_at,
                    "name": "处理约定" if goal.parameters.get("commitment_stream_id") else goal.name,
                    "description": "处理一件已约好的事" if goal.parameters.get("commitment_stream_id") else goal.description,
                    "goal_type": goal.goal_type, "goal_id": goal.goal_id,
                })
        return sorted(segments, key=lambda item: (item["start"], item["end"]))

    @property
    def started(self) -> bool:
        return self._started

    @property
    def goal_manager(self) -> Any:
        """底层同步管理器；调用方必须通过 ``run_db`` 使用它。"""
        if self._goal_manager is None:
            raise RuntimeError("日程引擎尚未启动")
        return self._goal_manager

    @property
    def snapshot_cache(self) -> ScheduleSnapshotCache:
        if self._snapshot is None:
            raise RuntimeError("日程引擎尚未启动")
        return self._snapshot

    # ---------- 查询 ----------

    def peek(self) -> Optional[ActivitySnapshot]:
        """读当前活动快照。纯内存，hook 安全。

        引擎未启动时返回 ``None``，让调用方走"无活动"分支，不抛异常。
        """
        if not self._started or not self._schedule_config().enabled or self._snapshot is None:
            return None
        return self._snapshot.peek()

    async def refresh(self, *, force: bool = False) -> Optional[ActivitySnapshot]:
        """刷新快照。后台循环调用，不要在 hook 里调。"""
        if self._snapshot is None:
            return None
        return await self._snapshot.refresh(force=force)

    def current_activity_payload(self) -> Dict[str, Any]:
        """当前活动的对外 API 结构（与 planning-v4 逐字段一致）。"""
        return ScheduleSnapshotCache.to_api_payload(self.peek())

    # ---------- 生成 ----------

    def build_generator(self) -> Any:
        """构造 ``ScheduleGenerator``。

        统一所有构造入口的配置来源——planning-v4 的 v4.4.4 修过一个 bug，就是
        ``auto_scheduler`` 用 ``config.schedule.model_dump()`` 构造，而 pydantic 模型
        里没有 ``bot_profile`` 字段，导致 prompt 里人设缺失。这里保证只有一个出口。
        """
        from .planner.schedule_generator import ScheduleGenerator

        return ScheduleGenerator(
            self.goal_manager,
            config=self.build_generator_config(),
            plugin=self._plugin,
            run_db=self.run_db,
        )

    def build_generator_config(self) -> Dict[str, Any]:
        """构造 ScheduleGenerator 用的 dict 配置。

        ``ScheduleGenerator`` 收的是普通 dict 而不是 pydantic 模型（见
        ``planner/schedule_generator.py:65``），所以这里做一次显式映射，
        顺便把 MaiTrace 的人格信息塞进 ``bot_profile``。
        """
        schedule_config = self._schedule_config()
        return {
            **schedule_config.model_dump(),
            "bot_profile": dict(self._bot_profile),
            "life_context": {key: self._life_state[key] for key in (
                "energy", "hunger", "mood_valence", "health_note", "sleep_phase", "scene", "current_location", "environment",
            ) if self._life_state.get("enabled") and key in self._life_state},
            "llm_log_dir": str(self._data_dir / "llm_logs"),
        }

    # ---------- 配置读取 ----------

    def _schedule_config(self) -> Any:
        return self._plugin.config.schedule

    def _config_timezone(self) -> str:
        return self._schedule_config().timezone

    def _config_cache_ttl(self) -> float:
        return float(self._schedule_config().cache_ttl)

    def _config_cross_day(self) -> bool:
        return bool(self._schedule_config().cross_day_activity)
