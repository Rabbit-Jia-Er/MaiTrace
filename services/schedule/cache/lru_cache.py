"""LRU 缓存实现 - 自主规划插件

线程/协程安全的 LRU 缓存，支持异步与同步接口。

性能特性：
    - 自动 TTL 被动过期（访问时检查）
    - 主动批量过期清理（cleanup_expired）
    - LRU 淘汰
    - 单线程异步环境下基于 GIL 的轻量锁

使用示例：
    >>> from cache.lru_cache import LRUCache
    >>> cache = LRUCache(max_size=100, ttl=300)
    >>> await cache.set("key", "value")
    >>> value = await cache.get("key")
"""

from collections import OrderedDict
from typing import Any, List, Optional, Tuple
import logging
import threading
import time


logger = logging.getLogger(__name__)


class LRUCache:
    """LRU 缓存，支持异步与同步接口。

    设计要点：
        - 缓存项格式：``(value, expire_time)`` —— 由 LRUCache 自己管理过期
        - 锁：``threading.RLock``。在 maibot 单进程单事件循环模型下，
          每次锁持有时间 < 1ms，对 asyncio 事件循环无可感知阻塞
        - TTL：构造时统一指定，外部不应再二次封装过期时间
        - 不变量：``__contains__`` / ``__getitem__`` / ``get`` 都做被动过期检查

    Args:
        max_size: 缓存最大项数（默认 100）
        ttl: 缓存项生存时间（秒，默认 300）
    """

    def __init__(self, max_size: int = 100, ttl: int = 300) -> None:
        self._store: OrderedDict[Any, Tuple[Any, float]] = OrderedDict()
        self.max_size: int = max_size
        self.ttl: int = ttl
        self._lock: threading.RLock = threading.RLock()

    # ------------------------------------------------------------
    # 内部辅助
    # ------------------------------------------------------------

    @staticmethod
    def _is_expired(expire_time: float) -> bool:
        """检查过期时间戳是否已过当前时间。"""
        return time.time() >= expire_time

    # ------------------------------------------------------------
    # 异步接口（推荐在协程代码中使用）
    # ------------------------------------------------------------

    async def get(self, key: Any) -> Optional[Any]:
        """获取缓存值（过期自动清理；同步逻辑由 RLock 保护）。"""
        with self._lock:
            if key not in self._store:
                return None
            value, expire_time = self._store[key]
            if self._is_expired(expire_time):
                del self._store[key]
                return None
            self._store.move_to_end(key)
            return value

    async def set(self, key: Any, value: Any) -> None:
        """写入缓存值，自动 LRU 淘汰。"""
        with self._lock:
            expire_time = time.time() + self.ttl
            if key in self._store:
                self._store.move_to_end(key)
            self._store[key] = (value, expire_time)
            if len(self._store) > self.max_size:
                self._store.popitem(last=False)

    # ------------------------------------------------------------
    # 主动批量清理（供后台任务调用）
    # ------------------------------------------------------------

    def cleanup_expired(self) -> int:
        """主动清理所有已过期的缓存项，返回清理数量。"""
        with self._lock:
            now = time.time()
            expired_keys = [key for key, (_, expire_time) in self._store.items() if now >= expire_time]
            for key in expired_keys:
                self._store.pop(key, None)
            if expired_keys:
                logger.debug(f"主动清理了 {len(expired_keys)} 个过期缓存项")
            return len(expired_keys)

    def clear(self) -> None:
        """清空全部缓存。"""
        with self._lock:
            self._store.clear()

    def items(self) -> List[Tuple[Any, Any]]:
        """返回所有未过期键值对（快照拷贝）。"""
        with self._lock:
            now = time.time()
            return [
                (key, value)
                for key, (value, expire_time) in self._store.items()
                if now < expire_time
            ]

    def __len__(self) -> int:
        """当前缓存项数量（包含已过期但尚未被清理的项）。"""
        with self._lock:
            return len(self._store)

    # ------------------------------------------------------------
    # 同步 dict-like 接口（保留以方便偶尔的同步访问场景）
    # ------------------------------------------------------------

    def __delitem__(self, key: Any) -> None:
        """同步删除缓存项。"""
        with self._lock:
            self._store.pop(key, None)

    def __contains__(self, key: Any) -> bool:
        """同步检查键是否存在且未过期。"""
        with self._lock:
            if key not in self._store:
                return False
            _, expire_time = self._store[key]
            if self._is_expired(expire_time):
                del self._store[key]
                return False
            return True

    def __getitem__(self, key: Any) -> Any:
        """同步获取（不更新 LRU 顺序；过期抛 KeyError）。"""
        with self._lock:
            if key not in self._store:
                raise KeyError(key)
            value, expire_time = self._store[key]
            if self._is_expired(expire_time):
                del self._store[key]
                raise KeyError(key)
            return value

    def __setitem__(self, key: Any, value: Any) -> None:
        """同步写入（等价于异步 set）。"""
        with self._lock:
            expire_time = time.time() + self.ttl
            if key in self._store:
                self._store.move_to_end(key)
            self._store[key] = (value, expire_time)
            if len(self._store) > self.max_size:
                self._store.popitem(last=False)
