"""SnowLuma 与 NapCat 入站消息的轻量归一化。"""
from __future__ import annotations

from collections.abc import Iterator, Mapping
from typing import Any


SUPPORTED_ADAPTERS = ("snowluma", "napcat")


def _additional_config(message: Mapping[str, Any]) -> Mapping[str, Any]:
    info = message.get("message_info")
    if not isinstance(info, Mapping):
        return {}
    additional = info.get("additional_config")
    return additional if isinstance(additional, Mapping) else {}


def adapter_name(message: Mapping[str, Any]) -> str:
    """从适配器保留的技术字段识别来源，不依赖展示名称。"""
    additional = _additional_config(message)
    # SnowLuma 通知会同时写入 napcat_* 兼容字段，必须优先识别自己的原生标记。
    if any(str(key).startswith("snowluma_") for key in additional):
        return "snowluma"
    if any(str(key).startswith("napcat_") for key in additional):
        return "napcat"
    return "unknown"


def recall_notice(message: Mapping[str, Any]) -> dict[str, str]:
    """把 SnowLuma/NapCat 的 OneBot 撤回通知归一化为同一结构。"""
    if not bool(message.get("is_notify")):
        return {}
    additional=_additional_config(message)
    adapter=adapter_name(message)
    if adapter=="snowluma":
        notice_type=str(additional.get("snowluma_notice_type") or additional.get("napcat_notice_type") or "").strip()
        payload=additional.get("snowluma_notice_payload") or additional.get("napcat_notice_payload")
    else:
        notice_type=str(additional.get("napcat_notice_type") or "").strip()
        payload=additional.get("napcat_notice_payload")
    if notice_type not in {"friend_recall","group_recall"} or not isinstance(payload,Mapping):
        return {}
    recalled_message_id=str(payload.get("message_id") or "").strip()
    if not recalled_message_id:return {}
    info=message.get("message_info") if isinstance(message.get("message_info"),Mapping) else {}
    group=info.get("group_info") if isinstance(info.get("group_info"),Mapping) else {}
    return {
        "notice_type":notice_type,
        "recalled_message_id":recalled_message_id,
        "user_id":str(payload.get("user_id") or "").strip(),
        "operator_id":str(payload.get("operator_id") or payload.get("user_id") or "").strip(),
        "group_id":str(payload.get("group_id") or group.get("group_id") or "").strip(),
        "self_id":str(payload.get("self_id") or additional.get("self_id") or "").strip(),
        "adapter":adapter,
    }


def group_identity(message: Mapping[str, Any]) -> tuple[str, str]:
    """读取 Host 标准群资料；两套适配器的私有字段只用于来源诊断。"""
    info=message.get("message_info")
    if not isinstance(info,Mapping):return "",""
    group=info.get("group_info")
    if not isinstance(group,Mapping):return "",""
    return str(group.get("group_id") or "").strip(),str(group.get("group_name") or "").strip()


def sender_identity(message: Mapping[str, Any]) -> tuple[str, str]:
    """读取 Host 标准发送者资料，不依赖 NapCat/SnowLuma 原始事件形状。"""
    info=message.get("message_info")
    if not isinstance(info,Mapping):return "",""
    user=info.get("user_info")
    if not isinstance(user,Mapping):return "",""
    user_id=str(user.get("user_id") or "").strip()
    name=str(user.get("user_cardname") or user.get("user_nickname") or "").strip()
    return user_id,name


def component_kind(component: Mapping[str, Any]) -> str:
    """展开 SnowLuma 用 ``dict`` 包装的 file/video 等媒介类型。"""
    kind = str(component.get("type") or "").strip().lower()
    data = component.get("data")
    if kind == "dict" and isinstance(data, Mapping):
        nested = str(data.get("type") or "").strip().lower()
        return nested or kind
    return kind


def component_text(component: Mapping[str, Any]) -> str:
    if component_kind(component) != "text":
        return ""
    data = component.get("data")
    if isinstance(data, str):
        return data
    if isinstance(data, Mapping):
        return str(data.get("text") or data.get("content") or "")
    return ""


def _forward_nodes(value: Any) -> list[Mapping[str, Any]]:
    if isinstance(value, list):
        return [item for item in value if isinstance(item, Mapping)]
    if not isinstance(value, Mapping):
        return []
    for key in ("messages", "content"):
        nested = value.get(key)
        if isinstance(nested, list):
            return [item for item in nested if isinstance(item, Mapping)]
    nested_data = value.get("data")
    return _forward_nodes(nested_data) if nested_data is not value else []


def _node_content(node: Mapping[str, Any]) -> Any:
    for key in ("content", "message"):
        value = node.get(key)
        if isinstance(value, list):
            return value
    data = node.get("data")
    if isinstance(data, Mapping):
        for key in ("content", "message"):
            value = data.get(key)
            if isinstance(value, list):
                return value
    return []


def walk_components(components: Any, *, _depth: int = 0) -> Iterator[dict[str, Any]]:
    """遍历标准消息段和两种适配器的合并转发节点。

    深度限制用于防止异常转发载荷构造循环或极深嵌套，避免阻塞消息 Hook。
    """
    if not isinstance(components, list) or _depth >= 8:
        return
    for item in components:
        if not isinstance(item, dict):
            continue
        yield item
        if component_kind(item) != "forward":
            continue
        for node in _forward_nodes(item.get("data")):
            yield from walk_components(_node_content(node), _depth=_depth + 1)


def reply_target_ids(message: Mapping[str, Any]) -> list[str]:
    targets: list[str] = []
    direct = str(message.get("reply_to") or "").strip()
    if direct:
        targets.append(direct)
    for item in walk_components(message.get("raw_message")):
        if component_kind(item) != "reply":
            continue
        data = item.get("data")
        if not isinstance(data, Mapping):
            continue
        target = str(data.get("target_message_id") or data.get("id") or "").strip()
        if target and target not in targets:
            targets.append(target)
    return targets
