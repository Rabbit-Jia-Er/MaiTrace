"""聊天流白名单匹配。

供 ``inject_service`` 和 ``command_service`` 共用：判断给定 session 是否
被允许参与日程注入 / 命令响应。

支持的白名单条目格式：
- ``all`` —— 全部允许
- ``session:<session_id>`` —— 精确匹配 session_id
- ``<session_id>`` —— 等价于 ``session:<session_id>``
- ``<platform>:group:<group_id>`` —— 匹配群聊
- ``<platform>:private:<user_id>`` —— 匹配私聊

为了向后兼容，``allowed_streams`` 为空列表时**视为全部允许**。
"""

from __future__ import annotations

from typing import Any, Dict, Iterable, List, Optional


def _normalize_entries(allowed_streams: Iterable[str]) -> List[str]:
    """过滤掉空白条目并 strip。"""
    return [str(item).strip() for item in allowed_streams if str(item).strip()]


def stream_match_keys(
    session_id: str,
    stream_info: Optional[Dict[str, Any]] = None,
) -> List[str]:
    """根据 session_id 和 stream_info 生成可能的白名单匹配键。

    Args:
        session_id: 当前会话 ID（HookHandler / Command 接收到的 stream_id）。
        stream_info: 可选的 stream 详细信息（来自 ``ctx.chat.get_all_streams``），
            含 ``platform`` / ``group_id`` / ``user_id`` 等字段。

    Returns:
        所有可用于白名单匹配的字符串列表。
    """
    keys: List[str] = []
    sid = (session_id or "").strip()
    if sid:
        keys.append(sid)
        keys.append(f"session:{sid}")

    if stream_info:
        platform = str(stream_info.get("platform") or "qq").strip() or "qq"
        group_id = str(stream_info.get("group_id") or "").strip()
        user_id = str(stream_info.get("user_id") or "").strip()
        if group_id:
            keys.append(f"{platform}:group:{group_id}")
        elif user_id:
            keys.append(f"{platform}:private:{user_id}")
    return keys


def is_stream_allowed(
    session_id: str,
    allowed_streams: Iterable[str],
    stream_info: Optional[Dict[str, Any]] = None,
) -> bool:
    """判断给定 session 是否在白名单内。

    Args:
        session_id: 当前会话 ID。
        allowed_streams: 白名单条目列表，来自 ``config.schedule.allowed_streams``。
        stream_info: 可选的 stream 详细信息，可帮助匹配 ``qq:group:xxx`` 这类条目。

    Returns:
        是否允许该 session 参与日程注入 / 命令响应。
    """
    entries = _normalize_entries(allowed_streams or [])
    if not entries:
        return True  # 留空视为全部允许（向后兼容）
    if any(item.lower() == "all" for item in entries):
        return True
    candidate_keys = set(stream_match_keys(session_id, stream_info))
    return any(entry in candidate_keys for entry in entries)
