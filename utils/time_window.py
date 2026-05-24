"""静默时间段解析。"""

import datetime
import logging
from ._logging import get_logger
from typing import Optional

logger = get_logger(__name__)


def _parse_time_to_minutes(time_str: str) -> Optional[int]:
    """'HH:MM' → 自 00:00 起的分钟数。失败返回 None。"""
    if not time_str or ":" not in time_str:
        return None
    try:
        hour_str, minute_str = time_str.split(":", 1)
        hour = int(hour_str.strip())
        minute = int(minute_str.strip())
        if 0 <= hour <= 23 and 0 <= minute <= 59:
            return hour * 60 + minute
    except (ValueError, AttributeError):
        pass
    return None


def is_in_silent_period(
    silent_hours_config: str,
    like_during_silent: bool = False,
    comment_during_silent: bool = False,
) -> tuple[bool, bool, bool]:
    """判定当前是否在静默时间段。

    Args:
        silent_hours_config: 例 "22:00-07:00,12:00-14:00"。
        like_during_silent: 静默期是否允许点赞。
        comment_during_silent: 静默期是否允许评论。

    Returns:
        (是否静默, 允许点赞, 允许评论)。非静默期 → (False, True, True)。
    """
    if not silent_hours_config or not silent_hours_config.strip():
        return False, True, True

    try:
        now = datetime.datetime.now()
        current = now.hour * 60 + now.minute

        for period in silent_hours_config.split(","):
            period = period.strip()
            if not period or "-" not in period:
                continue
            start_str, end_str = period.split("-", 1)
            start = _parse_time_to_minutes(start_str.strip())
            end = _parse_time_to_minutes(end_str.strip())
            if start is None or end is None:
                continue

            in_window = (start <= current <= end) if start <= end else (current >= start or current <= end)
            if in_window:
                return True, like_during_silent, comment_during_silent

        return False, True, True
    except Exception as exc:
        logger.error("解析静默时间段失败: %s", exc)
        return False, True, True
