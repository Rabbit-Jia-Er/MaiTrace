"""共享时区与本地历法，不获取外部天气或联网信息。"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Dict
from zoneinfo import ZoneInfo
import asyncio

from .config import EnvironmentSection
from .storage import LifeStore



class EnvironmentService:
    def __init__(self, store: LifeStore, config: EnvironmentSection, timezone: str) -> None:
        self.store = store
        self.config = config
        self.timezone = ZoneInfo(timezone)
        self._calendar: Any = None
        self._solar: Any = None

    def now(self) -> datetime:
        return datetime.now(self.timezone)

    async def start(self) -> None:
        if self.config.calendar_enabled:
            def load_calendar() -> Any:
                try:
                    from lunar_python import Solar
                    import chinese_calendar
                except ImportError as exc:
                    raise RuntimeError("历法已启用，但缺少 chinese-calendar 或 lunar-python；请安装插件声明依赖") from exc
                return chinese_calendar, Solar
            self._calendar, self._solar = await asyncio.to_thread(load_calendar)

    def snapshot(self) -> Dict[str, Any]:
        """只提供本地日期与历法，旧天气缓存不再读取或展示。"""
        now = self.now()
        calendar: Dict[str, Any] = {"date": now.date().isoformat(), "iso_time": now.isoformat(timespec="seconds"),
            "timezone": str(self.timezone), "weekday": now.weekday(), "is_workday": None,
            "holiday": "", "lunar": "", "solar_term": "", "calendar_available": False}
        if self.config.calendar_enabled:
            if self._calendar is None or self._solar is None:
                raise RuntimeError("历法尚未初始化")
            calendar["is_workday"] = bool(self._calendar.is_workday(now.date()))
            is_holiday, name = self._calendar.get_holiday_detail(now.date())
            calendar["holiday"] = str(name) if is_holiday and name else ""
            calendar["calendar_available"] = True
            lunar = self._solar.fromYmd(now.year, now.month, now.day).getLunar()
            calendar["lunar"], calendar["solar_term"] = str(lunar.toString()), str(lunar.getJieQi())
        return calendar
