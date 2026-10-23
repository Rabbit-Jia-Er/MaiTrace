"""基于内存日程快照的智能注入；Hook 内不请求模型、不访问数据库。"""

from .planner_items import InjectionEngine

__all__ = ["InjectionEngine"]
