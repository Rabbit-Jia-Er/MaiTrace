"""消费唯一 planning 时间线的生活引擎。"""

from typing import Any, TYPE_CHECKING

from .storage import LifeStore

if TYPE_CHECKING:
    from .engine import LifeEngine


def __getattr__(name: str) -> Any:
    # 配置、数据模型被素材子模块共用；仅在实际装配引擎时加载上层服务，避免循环导入。
    if name == "LifeEngine":
        from .engine import LifeEngine
        return LifeEngine
    raise AttributeError(name)

__all__ = ["LifeEngine", "LifeStore"]
