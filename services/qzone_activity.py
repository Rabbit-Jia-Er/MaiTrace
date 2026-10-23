"""QQ 空间成功活动的记录入口与有界模型上下文。"""

from __future__ import annotations

from typing import Any, Dict, List

from ..utils import get_logger
from ..utils.untrusted import wrap_untrusted

logger = get_logger(__name__)
_LABELS = {"publish": "发布说说", "comment": "发表评论", "skip_comment": "选择不评论", "reply": "回复评论"}


async def record_activity(plugin: Any, kind: str, **fields: Any) -> None:
    """记录失败不会影响已确认成功的空间动作；取消仍由调用者正常收口。"""
    try:
        await plugin.persistence.record_qzone_activity(kind, **fields)
    except Exception as exc:
        logger.warning("记录空间活动失败: %s", exc)


def format_activity(entries: List[Dict[str, Any]]) -> str:
    if not entries:
        return "空间活动记录中没有相关内容。"
    sections: List[str] = []
    for entry in entries:
        lines = [f"{entry.get('time', '')} | {_LABELS.get(str(entry.get('kind')), '空间活动')}"]
        for key, label in (("target_name", "对象"), ("target_qq", "对象QQ"), ("fid", "动态ID"),
                           ("feed", "说说背景"), ("comment", "对方评论"), ("text", "我写的")):
            value = str(entry.get(key) or "")[:1000]
            if value:
                lines.append(f"{label}：{value}")
        sections.append("\n".join(lines))
    return "以下是已记录的空间活动，请依据记录回答：\n" + wrap_untrusted(
        "\n\n".join(sections), label="空间活动历史", max_chars=12000,
    )
