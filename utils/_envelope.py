"""处理 ctx 返回的 success/error 信封。

新 SDK 的 ctx.* 调用大部分会被 _normalize_capability_result 自动剥壳，
但少数 capability 仍保留 {"success": bool, "data"/"messages"/"value": ...}
形态，本函数把两种形态统一成 raw payload。
"""

from typing import Any


def peel_envelope(value: Any) -> Any:
    """剥掉 success/data/value/messages 信封，返回原始 payload。

    规则：
    - 非 dict 直接返回。
    - dict 且 success=False → 返回原 dict（让调用方读 error）。
    - dict 含 data / value / messages 之一 → 返回该字段。
    - 其他 dict → 原样返回。
    """
    if not isinstance(value, dict):
        return value
    if value.get("success") is False:
        return value
    for key in ("data", "value", "messages"):
        if key in value:
            return value[key]
    return value
