"""统一使用显式配置的 IANA 时区，配置错误在启动时报告。"""

from __future__ import annotations

from datetime import datetime, tzinfo
from zoneinfo import ZoneInfo


class TimezoneManager:
    """时区管理器 - 集中管理时区处理，避免重复代码

    使用标准库 ``zoneinfo``；Windows 由声明的 ``tzdata`` 依赖提供 IANA 数据。
    不回退本机时区，避免跨天日程被悄悄移到另一个日期。

    Example:
        >>> tz_manager = TimezoneManager("Asia/Shanghai")
        >>> now = tz_manager.get_now()
        >>> print(now.strftime("%Y-%m-%d %H:%M:%S %Z"))
    """

    def __init__(self, timezone_str: str = "Asia/Shanghai") -> None:
        """初始化时区管理器。

        Args:
            timezone_str: 时区字符串（如 ``Asia/Shanghai``、``UTC``）。
        """
        self.timezone_str: str = timezone_str
        self._tz: tzinfo = self._init_timezone()

    def _init_timezone(self) -> tzinfo:
        """解析时区；名称或时区数据库有误时保留 ZoneInfo 的明确异常。"""
        return ZoneInfo(self.timezone_str)

    def get_now(self) -> datetime:
        """获取配置时区下带 tzinfo 的当前时间。"""
        return datetime.now(self._tz)
