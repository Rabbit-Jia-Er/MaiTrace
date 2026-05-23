"""日期解析与格式化。"""

import datetime
import re
from typing import Any, Optional


_RELATIVE_MAP = {"今天": 0, "昨天": -1, "前天": -2}


def parse_date(date_str: str) -> Optional[str]:
    """解析日期字符串，返回 YYYY-MM-DD 或 None。

    支持：今天/昨天/前天、YYYY-MM-DD、YYYY/MM/DD、YYYY.MM.DD。
    """
    if not date_str:
        return None
    if date_str in _RELATIVE_MAP:
        delta = _RELATIVE_MAP[date_str]
        return (datetime.datetime.now() + datetime.timedelta(days=delta)).strftime("%Y-%m-%d")
    for fmt in ("%Y-%m-%d", "%Y/%m/%d", "%Y.%m.%d"):
        try:
            return datetime.datetime.strptime(date_str, fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    if re.match(r"^\d{4}-\d{1,2}-\d{1,2}$", date_str):
        return date_str
    return None


def format_date_str(date_input: Any) -> str:
    """统一格式化为 YYYY-MM-DD。无法解析则抛 ValueError。"""
    if isinstance(date_input, datetime.datetime):
        return date_input.strftime("%Y-%m-%d")
    if isinstance(date_input, datetime.date):
        return date_input.strftime("%Y-%m-%d")
    if isinstance(date_input, str):
        parsed = parse_date(date_input)
        if parsed:
            return parsed
    raise ValueError(f"无法识别的日期格式: {date_input}。支持: YYYY-MM-DD / YYYY/MM/DD / YYYY.MM.DD / 今天 / 昨天 / 前天")


def today_str() -> str:
    """今天的 YYYY-MM-DD。"""
    return datetime.datetime.now().strftime("%Y-%m-%d")


def date_with_weather(date: str, weather: str) -> str:
    """组合 '2025年1月1日,星期一,晴。'。"""
    try:
        date_obj = datetime.datetime.strptime(date, "%Y-%m-%d")
        weekdays = ["星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日"]
        weekday = weekdays[date_obj.weekday()]
        return f"{date_obj.year}年{date_obj.month}月{date_obj.day}日,{weekday},{weather}。"
    except ValueError:
        return f"{date},{weather}。"
