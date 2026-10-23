"""按请求会话隐藏约定详情，保留全局日程的占用时段。"""

from copy import copy

from .planner.goal_manager import Goal


def goal_for_stream(goal: Goal, stream_id: str) -> Goal:
    """返回可展示的目标；其他会话的约定不携带标题、备注或身份字段。"""
    target_stream_id = str(
        goal.parameters.get("commitment_stream_id") or goal.parameters.get("target_stream_id") or ""
    ).strip()
    if not target_stream_id or target_stream_id == stream_id:
        return goal

    # 只改展示副本，不修改数据库目标或共享参数。
    visible = copy(goal)
    visible.name = "处理约定"
    visible.description = ""
    visible.creator_id = ""
    visible.chat_id = "global"
    visible.parameters = {"time_window": goal.parameters["time_window"]} if "time_window" in goal.parameters else {}
    visible.conditions = {"time_window": goal.conditions["time_window"]} if "time_window" in goal.conditions else {}
    return visible
