"""日程时间工具；时间窗统一使用从当天零点起的整数分钟数。

跨夜结束时间可以超过 1440，例如 23:00 到次日 01:00 为 [1380, 1500]。
"""

from typing import List, Optional, Tuple

from ..core.exceptions import InvalidTimeWindowError
from ..core.parameter_validator import ParameterValidator


def parse_time_window(time_window: Optional[List[int]]) -> Tuple[Optional[int], Optional[int]]:
    """读取分钟制时间窗，无效时返回 (None, None)。"""
    try:
        ParameterValidator.validate_time_window(time_window)
    except InvalidTimeWindowError:
        return None, None
    return time_window[0], time_window[1]


def parse_time_slot(time_slot: str) -> Tuple[Optional[int], Optional[int]]:
    """
    解析 HH:MM 格式时间字符串为分钟数

    Args:
        time_slot: 时间字符串，如 "09:30"

    Returns:
        (hour, minute) 或 (None, None)
    """
    if not time_slot or not isinstance(time_slot, str):
        return None, None

    try:
        parts = time_slot.split(":")
        hour = int(parts[0])
        minute = int(parts[1]) if len(parts) > 1 else 0
        return hour, minute
    except (ValueError, IndexError):
        return None, None


def time_slot_to_minutes(time_slot: str) -> Optional[int]:
    """
    将 HH:MM 格式时间转换为从00:00开始的分钟数

    Args:
        time_slot: 时间字符串，如 "09:30"

    Returns:
        分钟数（如 570 表示 09:30）或 None
    """
    hour, minute = parse_time_slot(time_slot)
    if hour is None:
        return None
    return hour * 60 + minute


def format_minutes_to_time(minutes: int) -> str:
    """
    将分钟数格式化为 HH:MM 字符串

    Args:
        minutes: 从00:00开始的分钟数

    Returns:
        格式化的时间字符串，如 "09:30"
    """
    hour = minutes // 60
    minute = minutes % 60
    return f"{hour:02d}:{minute:02d}"
