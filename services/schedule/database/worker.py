"""日程 SQLite 专用执行线程：连接创建、查询、事务和关闭始终在同一线程。"""

from concurrent.futures import ThreadPoolExecutor
from functools import partial
from typing import Any, Callable, Optional, TypeVar
import asyncio


_Result = TypeVar("_Result")


class DatabaseWorker:
    """串行执行同步数据库操作，避免阻塞聊天事件循环及遗留线程本地连接。"""

    def __init__(self) -> None:
        self._executor: Optional[ThreadPoolExecutor] = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="maitrace-schedule-db"
        )

    async def call(self, function: Callable[..., _Result], *args: Any, **kwargs: Any) -> _Result:
        executor = self._executor
        if executor is None:
            raise RuntimeError("日程数据库执行器已关闭")
        return await asyncio.get_running_loop().run_in_executor(
            executor, partial(function, *args, **kwargs)
        )

    async def close(self, close_connection: Optional[Callable[[], None]] = None) -> None:
        executor = self._executor
        if executor is None:
            return
        # 一开始就拒绝新查询，关闭连接排在此前已经入队的 SQL 后面。
        self._executor = None
        try:
            if close_connection is not None:
                await asyncio.get_running_loop().run_in_executor(executor, close_connection)
        finally:
            # ScheduleEngine 用受保护的生命周期任务调用此方法，取消不会中断释放。
            await asyncio.to_thread(executor.shutdown, wait=True, cancel_futures=False)
