"""当前日程查询：输入 GoalManager 和当前时间，输出结构化活动。

**本模块是同步的**，因为 ``GoalManager`` / ``GoalDatabase`` 是同步 sqlite 访问
（``database/goal_db.py`` 用 ``threading.local()`` 持连接）。调用方负责用
引擎的 ``run_db`` 调度到数据库线程——见 ``snapshot.py``。

时间窗口约定：
    ``goal.parameters["time_window"]`` 是 ``[start, end]`` 两个整数分钟数。
    - 跨夜活动的 ``end`` 会超过 1440（例如 23:00-07:00 记作 ``[1380, 1860]``）
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from .utils.time_utils import parse_time_window

# 日程活动之外的 goal 类型：约定候选不是"此刻在做的事"，查询时要跳过
_NON_ACTIVITY_GOAL_TYPES = frozenset({"pending_commitment"})

_MINUTES_PER_DAY = 1440


@dataclass(frozen=True)
class CurrentActivity:
    """当前时间落在其时间窗内的那个活动。"""

    name: str
    description: str
    goal_type: str
    time_window: Optional[str] = None
    """``"HH:MM-HH:MM"``；跨夜活动的结束时间已归一化到 24 小时内。"""
    goal_id: str = ""
    parameters: Dict[str, Any] = field(default_factory=dict)
    start_at: Optional[datetime] = None
    end_at: Optional[datetime] = None


@dataclass(frozen=True)
class ScheduleQueryResult:
    """一次日程查询的结果。"""

    current: Optional[CurrentActivity]
    next_activities: Tuple[Tuple[str, str], ...] = ()
    """``(("18:15", "操场散步"), ...)``，按开始时间升序，仅含今天创建且尚未开始的。"""
    previous_activity: Optional[CurrentActivity] = None
    commitments: Tuple[Dict[str, Any], ...] = ()
    next_activity_details: Tuple[CurrentActivity, ...] = ()

    @property
    def has_activity(self) -> bool:
        return self.current is not None


def _format_time_window(start_minutes: int, end_minutes: int) -> str:
    """把 ``[start, end]`` 分钟数格式化成 ``HH:MM-HH:MM``。"""
    start_str = f"{start_minutes // 60:02d}:{start_minutes % 60:02d}"
    # 跨夜活动 end 会超过 1440，展示时归一化回 24 小时内
    normalized_end = end_minutes if end_minutes < _MINUTES_PER_DAY else end_minutes - _MINUTES_PER_DAY
    end_str = f"{normalized_end // 60:02d}:{normalized_end % 60:02d}"
    return f"{start_str}-{end_str}"


def _extract_time_window(goal: Any) -> Optional[List[int]]:
    """从 goal.parameters 读取分钟制时间窗。"""
    return goal.parameters.get("time_window")


def _created_on(goal: Any, date_str: str) -> bool:
    """判断 goal 是否创建于指定日期。"""
    created_at = goal.created_at
    if not created_at:
        return False
    return created_at.strftime("%Y-%m-%d") == date_str


def _is_in_window(
    current_minutes: int,
    start_minutes: int,
    end_minutes: int,
    *,
    is_today: bool,
    is_yesterday: bool,
) -> bool:
    """判断当前时刻是否落在活动的时间窗内。

    跨夜活动（``end_minutes > 1440``）要分两段判：今天创建的只看"今晚那段"，
    昨天创建的只看"今晨延续的那段"。
    """
    is_cross_day = end_minutes > _MINUTES_PER_DAY

    if is_today:
        if not is_cross_day:
            return start_minutes <= current_minutes < end_minutes
        # 今晚 23 点的活动不能在今天凌晨 1 点提前命中。
        return start_minutes <= current_minutes < _MINUTES_PER_DAY

    if is_yesterday and is_cross_day:
        # 昨天开始的跨夜活动，今天凌晨还没结束的那一段
        return 0 <= current_minutes < (end_minutes - _MINUTES_PER_DAY)

    return False


def query_current_schedule(
    goal_manager: Any,
    *,
    now: datetime,
    chat_id: str = "global",
    cross_day_activity: bool = True,
) -> ScheduleQueryResult:
    """查询当前时刻的日程活动。

    Args:
        goal_manager: 已初始化的 ``GoalManager``。
        now: 当前时间（调用方负责按配置时区取，见 ``utils/timezone_manager.py``）。
        chat_id: 作用域。先查 ``global``，查不到再退回该 ``chat_id``。
        cross_day_activity: 是否接受昨天创建的跨夜活动延续到今天凌晨。

    Returns:
        ScheduleQueryResult: 当前活动 + 今天尚未开始的后续活动。

    Note:
        同步函数，内部有 sqlite 访问。**不要在 hook 里直接调**——maisaka hook 只有
        6 秒预算，走 ``snapshot.ScheduleSnapshotCache.peek()`` 读内存。
    """
    goals = goal_manager.get_active_goals(chat_id="global")
    if not goals and chat_id and chat_id != "global":
        goals = goal_manager.get_active_goals(chat_id=chat_id)
    if not goals:
        return ScheduleQueryResult(current=None)

    current_minutes = now.hour * 60 + now.minute
    today_str = now.strftime("%Y-%m-%d")
    yesterday_str = (now - timedelta(days=1)).strftime("%Y-%m-%d")

    # (goal, time_window, is_today, is_yesterday)
    scheduled: List[Tuple[Any, List[int], bool, bool]] = []
    for goal in goals:
        if goal.goal_type in _NON_ACTIVITY_GOAL_TYPES:
            continue
        time_window = _extract_time_window(goal)
        if parse_time_window(time_window)[0] is None:
            continue
        is_today = _created_on(goal, today_str)
        is_yesterday = _created_on(goal, yesterday_str)
        if is_today or (cross_day_activity and is_yesterday):
            scheduled.append((goal, time_window, is_today, is_yesterday))

    scheduled.sort(key=lambda item: item[1][0])

    current: Optional[CurrentActivity] = None
    current_created_at: Any = None

    for goal, time_window, is_today, is_yesterday in scheduled:
        start_minutes, end_minutes = time_window
        if not _is_in_window(
            current_minutes, start_minutes, end_minutes,
            is_today=is_today, is_yesterday=is_yesterday,
        ):
            continue
        # 多个活动同时命中时取创建时间最新的。
        if current is not None and goal.created_at and current_created_at is not None:
            if goal.created_at <= current_created_at:
                continue
        current = CurrentActivity(
            name=goal.name,
            description=goal.description or goal.name,
            goal_type=goal.goal_type or "",
            time_window=_format_time_window(start_minutes, end_minutes),
            goal_id=goal.goal_id,
            parameters=dict(goal.parameters),
            start_at=datetime.combine(
                now.date() if is_today else (now - timedelta(days=1)).date(),
                datetime.min.time(), tzinfo=now.tzinfo,
            ) + timedelta(minutes=start_minutes),
            end_at=datetime.combine(
                now.date() if is_today else (now - timedelta(days=1)).date(),
                datetime.min.time(), tzinfo=now.tzinfo,
            ) + timedelta(minutes=end_minutes),
        )
        current_created_at = goal.created_at

    # 今天尚未开始的活动
    next_activities: List[Tuple[str, str]] = []
    next_details: List[CurrentActivity] = []
    for goal, time_window, is_today, _is_yesterday in scheduled:
        if not is_today:
            continue
        start_minutes = time_window[0]
        if start_minutes > current_minutes:
            next_activities.append(
                (f"{start_minutes // 60:02d}:{start_minutes % 60:02d}", goal.name)
            )
            day = now.replace(hour=0, minute=0, second=0, microsecond=0)
            next_details.append(CurrentActivity(goal.name, goal.description or goal.name, goal.goal_type,
                _format_time_window(*time_window), goal.goal_id, dict(goal.parameters),
                day + timedelta(minutes=time_window[0]), day + timedelta(minutes=time_window[1])))

    previous = None
    # 到期清理会将旧活动标记完成，过渡背景仍需读到它们；取消或暂停的活动不算亲历。
    for goal in goal_manager.get_schedule_goals(chat_id="global", include_yesterday=cross_day_activity):
        if goal.status.value not in {"active", "completed"}:
            continue
        window = _extract_time_window(goal)
        if parse_time_window(window)[0] is None:
            continue
        day = goal.created_at.date()
        begin = datetime.combine(day, datetime.min.time(), tzinfo=now.tzinfo) + timedelta(minutes=window[0])
        end = datetime.combine(day, datetime.min.time(), tzinfo=now.tzinfo) + timedelta(minutes=window[1])
        if end <= now and (previous is None or previous.end_at < end):
            previous = CurrentActivity(goal.name, goal.description or goal.name, goal.goal_type,
                _format_time_window(*window), goal.goal_id, dict(goal.parameters), begin, end)
    commitments = []
    for goal in goals:
        params = goal.parameters
        if goal.goal_type == "pending_commitment":
            target = str(params.get("commitment_date", ""))
            time_text, title = str(params.get("time", "")), goal.name
            stream_id, notes = str(params.get("target_stream_id", "")), goal.description
        elif params.get("is_commitment"):
            target = str(params.get("commitment_date", goal.created_at.date().isoformat()))
            time_text, title = str(params.get("commitment_time", "")), str(params.get("commitment_title", goal.name))
            stream_id, notes = str(params.get("commitment_stream_id", "")), str(params.get("commitment_notes", ""))
            window = _extract_time_window(goal)
            if target == today_str and window is not None and window[1] <= current_minutes:
                continue
        else:
            continue
        if target >= today_str:
            commitments.append({"date": target, "time": time_text, "title": title,
                "target_stream_id": stream_id, "notes": notes,
                "duration_minutes": params.get("duration_minutes", 30)})
    commitments.sort(key=lambda entry: (entry["date"], entry["time"]))
    return ScheduleQueryResult(current=current, next_activities=tuple(next_activities),
        previous_activity=previous, commitments=tuple(commitments), next_activity_details=tuple(next_details))
