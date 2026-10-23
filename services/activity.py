from dataclasses import dataclass
from enum import Enum
from typing import Dict


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
    COOKING = "cooking"
    SHOPPING = "shopping"
    GAMING = "gaming"
    BATHING = "bathing"
    OTHER = "other"


@dataclass
class ActivityInfo:
    activity_type: ActivityType
    description: str
    mood: str = "neutral"
    time_point: str = ""


_TYPE_MAP: Dict[str, ActivityType] = {
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
    "cooking": ActivityType.COOKING,
    "shopping": ActivityType.SHOPPING,
    "gaming": ActivityType.GAMING,
    "bathing": ActivityType.BATHING,
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
    """优先保留明确类型，并将日程 meal/study 等转换成绘卷的稳定枚举。"""
    normalized = goal_type.strip().lower()
    for activity in ActivityType:
        if normalized == activity.value:
            return activity
    # 日程的 rest/daily_routine 等类别比较粗，睡眠与洗漱由场景进一步细分。
    for keywords, activity in (
        (("起床", "睡醒", "醒来"), ActivityType.WAKING_UP),
        (("睡觉", "入睡", "午睡", "睡眠", "午休", "小憩"), ActivityType.SLEEPING),
        (("洗澡", "泡澡", "沐浴", "洗漱", "护肤", "梳妆"), ActivityType.SELF_CARE),
    ):
        if any(word in description for word in keywords):
            return activity
    exact = {
        "meal": ActivityType.EATING, "study": ActivityType.STUDYING,
        "learn_topic": ActivityType.STUDYING, "exercise": ActivityType.EXERCISING,
        "entertainment": ActivityType.HOBBY, "social_maintenance": ActivityType.SOCIALIZING,
        "health_check": ActivityType.SELF_CARE, "free_time": ActivityType.RELAXING,
    }
    if normalized in exact:
        return exact[normalized]
    combined = (goal_type + " " + description).lower()
    for key, atype in _TYPE_MAP.items():
        if key in combined:
            return atype
    return ActivityType.OTHER


"""将内部目标类型统一为生活、Routine 与绘卷共用的活动类型。"""
