"""当前活动的内存快照 —— hook 路径专用。

## 为什么必须有这一层

``maisaka.*`` 系列 hook 的超时是 **6 秒**（``src/maisaka/chat_loop_service.py``
里每个 HookSpec 的 ``default_timeout_ms=6000``），而且 ``allow_abort=False`` ——
超时不会被"跳过"，只会让整轮聊天卡住。

同时 ``GoalManager`` / ``GoalDatabase`` 是**全同步**的 sqlite 访问
（``database/goal_db.py`` 用 ``threading.local()`` 持连接）。在 hook 里直接查库
等于把不确定的磁盘 I/O 和潜在的 SQLite 锁竞争塞进 6 秒预算，代价是拖垮麦麦的
整条聊天链路——而这个插件本来只该影响"发不发说说"。

## 约定

- **hook 只调 ``peek()``**：纯内存读，无锁、无 await、无 I/O。
- **只有后台循环 / Tool / Command 调 ``refresh()``**：通过引擎的 ``run_db``
  把同步查询交给连接所属的数据库线程。
- ``peek()`` 里以后也不要加任何查询。真要加数据，先在 ``refresh()`` 里算好塞进
  ``ActivitySnapshot``。

## 陈旧数据的取舍

``peek()`` 返回的可能是最多 ``ttl_seconds`` 之前的数据。对"麦麦现在在干嘛"
这个用途，几分钟的滞后完全无所谓——日程活动本来就是以十几分钟为粒度的。
拿准确性换 hook 的确定性，是划算的。
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Awaitable, Callable, Dict, List, Optional, Tuple
import asyncio
import time

from ...utils import get_logger
from .query import CurrentActivity, ScheduleQueryResult, query_current_schedule

logger = get_logger(__name__)

_DEFAULT_TTL_SECONDS = 300.0


@dataclass(frozen=True)
class ActivitySnapshot:
    """某一时刻的日程快照。不可变——``peek()`` 的调用方拿到就能直接读。"""

    current: Optional[CurrentActivity]
    next_activities: Tuple[Tuple[str, str], ...]
    as_of: str
    """快照对应的时间（ISO8601，秒精度）。"""
    timezone: str
    refreshed_at: float
    """``time.monotonic()`` 取的刷新时刻，只用于算 TTL。"""
    previous_activity: Optional[CurrentActivity] = None
    commitments: Tuple[Dict[str, Any], ...] = ()
    next_activity_details: Tuple[CurrentActivity, ...] = ()

    @property
    def has_activity(self) -> bool:
        return self.current is not None


class ScheduleSnapshotCache:
    """按 TTL 刷新的当前活动缓存。

    用法::

        cache = ScheduleSnapshotCache(goal_manager, tz_manager, run_db=engine.run_db)
        await cache.refresh()                # 后台循环里
        snap = cache.peek()                  # hook 里，纯内存
    """

    def __init__(
        self,
        goal_manager: Any,
        timezone_manager: Any,
        *,
        ttl_seconds: float = _DEFAULT_TTL_SECONDS,
        chat_id: str = "global",
        cross_day_activity: bool = True,
        run_db: Callable[..., Awaitable[Any]],
    ) -> None:
        """
        Args:
            goal_manager: 已初始化的 ``GoalManager``（同步）。
            timezone_manager: ``utils.timezone_manager.TimezoneManager``，提供
                ``get_now()`` 和 ``timezone_str``。
            ttl_seconds: 快照有效期。超过后 ``is_stale()`` 为真，但 ``peek()``
                仍返回旧值——宁可给略旧的数据，也不让 hook 等 I/O。
            chat_id: 查询作用域。
            cross_day_activity: 是否接受昨天创建的跨夜活动。
            run_db: 引擎的数据库调度入口，确保查询和连接生命周期属于同一线程。
        """
        self._goal_manager = goal_manager
        self._tz = timezone_manager
        self._ttl = max(1.0, float(ttl_seconds))
        self._chat_id = chat_id or "global"
        self._cross_day = bool(cross_day_activity)
        self._run_db = run_db
        self._snapshot: Optional[ActivitySnapshot] = None
        # 只保护 refresh 的并发，peek 完全不加锁
        self._refresh_lock = asyncio.Lock()

    # ---------- hook 路径：纯内存 ----------

    def peek(self) -> Optional[ActivitySnapshot]:
        """返回当前快照。**无锁、无 await、无 I/O。**

        Returns:
            Optional[ActivitySnapshot]: 还没刷新过时返回 ``None``；
            数据过期也照常返回（陈旧优于阻塞，理由见模块 docstring）。
        """
        return self._snapshot

    def is_stale(self) -> bool:
        """快照是否已超过 TTL。刷新调度用，``peek()`` 不看这个。"""
        if self._snapshot is None:
            return True
        return (time.monotonic() - self._snapshot.refreshed_at) >= self._ttl

    def invalidate(self) -> None:
        """丢弃当前快照。日程被重新生成后调用，让下次 refresh 一定重查。"""
        self._snapshot = None

    # ---------- 后台路径：允许 I/O ----------

    async def refresh(self, *, force: bool = False) -> Optional[ActivitySnapshot]:
        """重新查询日程并更新快照。

        Args:
            force: 为真时忽略 TTL 强制重查。

        Returns:
            Optional[ActivitySnapshot]: 最新快照；查询失败时丢弃旧快照并抛出错误。
        """
        if not force and not self.is_stale():
            return self._snapshot

        async with self._refresh_lock:
            # 等锁期间可能已经有人刷过了
            if not force and not self.is_stale():
                return self._snapshot

            now = self._now()
            try:
                # GoalManager 是同步的，交给专用数据库线程，避免阻塞事件循环。
                result: ScheduleQueryResult = await self._run_db(
                    query_current_schedule,
                    self._goal_manager,
                    now=now,
                    chat_id=self._chat_id,
                    cross_day_activity=self._cross_day,
                )
            except Exception:
                # 查询失败及时暴露，不把过期活动作为成功结果继续注入聊天。
                self._snapshot = None
                raise

            self._snapshot = ActivitySnapshot(
                current=result.current,
                next_activities=result.next_activities,
                as_of=now.isoformat(timespec="seconds"),
                timezone=self._timezone_str(),
                refreshed_at=time.monotonic(),
                previous_activity=result.previous_activity,
                commitments=result.commitments,
                next_activity_details=result.next_activity_details,
            )
            if result.current is not None:
                logger.debug("日程快照已更新: %s", result.current.name)
            else:
                logger.debug("日程快照已更新: 当前无活动")
            return self._snapshot

    # ---------- 对外结构 ----------

    @staticmethod
    def to_api_payload(snapshot: Optional[ActivitySnapshot]) -> Dict[str, Any]:
        """转成对外 API 的返回结构。

        字段与 planning-v4 的 ``get_current_activity`` 一致，麦麦绘卷可以继续通过
        ``_snapshot_to_activity()`` 解析，无需修改 ``ActivityInfo`` 的基础字段。

        Returns:
            Dict[str, Any]::

                {"has_activity": bool,
                 "activity": None | {"name","description","goal_type","time_window"},
                 "next_activities": [{"time","name"}, ...],
                 "as_of": str, "timezone": str, "error": None|str}
        """
        if snapshot is None:
            return {
                "has_activity": False,
                "activity": None,
                "next_activities": [],
                "as_of": "",
                "timezone": "",
                "error": "snapshot_not_ready",
            }

        activity: Optional[Dict[str, Any]] = None
        if snapshot.current is not None:
            private = bool(snapshot.current.parameters.get("commitment_stream_id"))
            activity = {
                "name": "处理约定" if private else snapshot.current.name,
                "description": "正在处理一件已约好的事" if private else snapshot.current.description,
                "goal_type": snapshot.current.goal_type,
                "time_window": snapshot.current.time_window,
                "goal_id": snapshot.current.goal_id,
                "parameters": {key: value for key, value in snapshot.current.parameters.items()
                               if key in {"location", "outfit", "scene_hint"} and not private},
                "start_at": snapshot.current.start_at.isoformat() if snapshot.current.start_at else "",
                "end_at": snapshot.current.end_at.isoformat() if snapshot.current.end_at else "",
            }

        next_list: List[Dict[str, str]] = [
            {"time": time_str, "name": name}
            for time_str, name in snapshot.next_activities
        ]
        if snapshot.next_activity_details:
            next_list = [{"time": entry.start_at.strftime("%H:%M"),
                          "name": "处理约定" if entry.parameters.get("commitment_stream_id") else entry.name}
                         for entry in snapshot.next_activity_details]
        return {
            "has_activity": snapshot.has_activity,
            "activity": activity,
            "next_activities": next_list,
            "as_of": snapshot.as_of,
            "timezone": snapshot.timezone,
            # 成功路径显式给 None，让调用方能统一用 result.get("error") 判断
            "error": None,
        }

    # ---------- 内部 ----------

    def _now(self) -> datetime:
        return self._tz.get_now()

    def _timezone_str(self) -> str:
        return self._tz.timezone_str
