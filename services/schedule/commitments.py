"""约定的日期校验和空闲时段；只插入新安排，不挪动已有活动。"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Iterable, List, Optional, Tuple
import re


DEFAULT_DURATION_MINUTES = 30


def validate_commitment_date(target: date, today: date) -> None:
    """允许今天及任意未来日期，拒绝新建过去日期的约定。"""
    if target < today:
        raise ValueError("不能约定过去的日期，原日程保持不变")


def infer_requested_date(text: str, today: date) -> Optional[date]:
    """裁判关闭时仍按明确日期登记，不把未来请求误记为今天。"""
    explicit = re.search(r"(?<!\d)(\d{4})[-/年](\d{1,2})[-/月](\d{1,2})(?:日)?(?!\d)", text)
    if explicit:
        return date(*(int(part) for part in explicit.groups()))
    offset = re.search(r"(\d+|[零〇一二两三四五六七八九十百千万]+)\s*天[后後]", text)
    if offset:
        number = offset[1]
        days = _parse_day_count(number)
        return today + timedelta(days=days)
    for word, days in (("大后天", 3), ("大後天", 3), ("后天", 2), ("後天", 2),
                       ("明天", 1), ("明日", 1), ("今天", 0), ("今日", 0)):
        if word in text:
            return today + timedelta(days=days)
    weekday = re.search(r"(下周|下星期|周|星期)([一二三四五六日天])", text)
    if weekday:
        target = "一二三四五六日".index(weekday[2].replace("天", "日"))
        days = (target - today.weekday()) % 7
        if weekday[1].startswith("下"):
            days = 7 - today.weekday() + target
        return today + timedelta(days=days)
    return None


def _parse_day_count(text: str) -> int:
    """解析数字及常用中文天数，避免把“十四天后”误读成“四天后”。"""
    if text.isdecimal():
        return int(text)
    digits = {char: index for index, char in enumerate("零一二三四五六七八九")}
    digits.update({"〇": 0, "两": 2})
    units = {"十": 10, "百": 100, "千": 1000}
    total = section = number = 0
    for char in text:
        if char in digits:
            number = number * 10 + digits[char]
        elif char in units:
            section += (number or 1) * units[char]
            number = 0
        else:  # 万
            total += (section + number or 1) * 10000
            section = number = 0
    return total + section + number


def validate_duration(value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 15 <= value <= 720:
        raise ValueError("约定时长必须为 15 至 720 分钟的整数")
    return value


def free_windows(
    now: datetime, occupied: Iterable[Tuple[datetime, datetime]],
) -> List[Tuple[datetime, datetime]]:
    """合并已占用区间后返回今天剩余的空档，也考虑昨天延续的跨夜活动。"""
    midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
    end_of_day = midnight + timedelta(days=1)
    cursor = now.replace(second=0, microsecond=0)
    if cursor < now:
        cursor += timedelta(minutes=1)
    windows = []
    for start, end in sorted(occupied):
        if end <= cursor or start >= end_of_day:
            continue
        if start > cursor:
            windows.append((cursor, min(start, end_of_day)))
        cursor = max(cursor, end)
        if cursor >= end_of_day:
            break
    if cursor < end_of_day:
        windows.append((cursor, end_of_day))
    return windows


class CommitmentConflictError(ValueError):
    """约定无法插入时，给出可以继续协商的时段。"""

    def __init__(self, message: str, windows: List[Tuple[datetime, datetime]], duration: int) -> None:
        self.available_times = [
            f"{start:%H:%M}–{end:%H:%M}" for start, end in windows
            if (end - start).total_seconds() >= duration * 60
        ][:3]
        suggestion = "；可用空档：" + "、".join(self.available_times) if self.available_times else "；今天没有足够空档，可以另约未来日期"
        super().__init__(message + suggestion + "。已有日程保持不变")
