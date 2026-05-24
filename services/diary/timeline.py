"""聊天时间线构建 + 情感天气推断 + 图片检测。

新 SDK 下消息形态是 dict（host 序列化），用 msg.get(...) 访问。
"""

from __future__ import annotations

import datetime
import logging
from ...utils import get_logger
import random
import re
from typing import Any, Dict, List, Tuple

logger = get_logger(__name__)


def _msg_time(msg: Dict[str, Any]) -> float:
    raw = msg.get("timestamp", 0) or msg.get("time", 0)
    try:
        return float(raw)
    except (TypeError, ValueError):
        return 0.0


def _msg_text(msg: Dict[str, Any]) -> str:
    return str(msg.get("processed_plain_text") or "")


def _msg_user(msg: Dict[str, Any]) -> Tuple[str, str]:
    info = msg.get("message_info") or {}
    user = info.get("user_info") or {}
    return (
        str(user.get("user_id", "") or ""),
        str(user.get("user_nickname") or user.get("user_cardname") or "某人"),
    )


def _is_image(msg: Dict[str, Any]) -> bool:
    """是图片消息吗？"""
    if msg.get("is_picture") or msg.get("is_picid"):
        return True
    text = _msg_text(msg).lower()
    if re.search(r"\[picid:[a-f0-9\-]+\]", text):
        return True
    if "[图片" in text or "[image" in text:
        return True
    raw = msg.get("raw_message") or []
    if isinstance(raw, list):
        for seg in raw:
            if isinstance(seg, dict) and str(seg.get("type", "")).lower() in ("image", "picture", "img"):
                return True
    return False


def _image_description(msg: Dict[str, Any]) -> str:
    """从 raw_message 找图片描述/alt 文本。"""
    raw = msg.get("raw_message") or []
    if not isinstance(raw, list):
        return ""
    for seg in raw:
        if not isinstance(seg, dict):
            continue
        if str(seg.get("type", "")).lower() not in ("image", "picture", "img"):
            continue
        data = seg.get("data") or {}
        if isinstance(data, dict):
            for key in ("description", "summary", "alt", "file"):
                val = data.get(key)
                if val:
                    return str(val)
    return ""


class TimelineBuilder:
    """构建按小时分段的聊天时间线。"""

    def __init__(
        self,
        bot_qq_account: str = "",
        *,
        per_message_max_chars: int = 200,
    ) -> None:
        """
        Args:
            bot_qq_account: bot 自己的 QQ，用于把 bot 的消息标记为"我"。
            per_message_max_chars: 单条消息文本最大字符数，超出截断为
                ``<前缀>...``。``0`` 表示不截断（最后会有整体 token 截断兜底）。
        """
        self.bot_qq_account = str(bot_qq_account or "")
        self.per_message_max_chars = max(0, int(per_message_max_chars or 0))
        self._stats: Dict[str, int] = {
            "total_messages": 0,
            "bot_messages": 0,
            "user_messages": 0,
        }

    @property
    def stats(self) -> Dict[str, int]:
        return dict(self._stats)

    def build(self, messages: List[Dict[str, Any]]) -> str:
        """聚合消息列表为时间线文本。"""
        if not messages:
            self._stats = {"total_messages": 0, "bot_messages": 0, "user_messages": 0}
            return "今天没有什么特别的对话。"

        parts: List[str] = []
        current_hour = -1
        bot_count = user_count = 0

        for msg in messages:
            ts = _msg_time(msg)
            try:
                dt = datetime.datetime.fromtimestamp(ts)
            except (OSError, OverflowError, ValueError):
                continue
            hour = dt.hour
            if hour != current_hour:
                if 6 <= hour < 12:
                    period = f"上午{hour}点"
                elif 12 <= hour < 18:
                    period = f"下午{hour}点"
                else:
                    period = f"晚上{hour}点"
                parts.append(f"\n【{period}】")
                current_hour = hour

            user_id, nickname = _msg_user(msg)
            is_bot = bool(self.bot_qq_account) and user_id == self.bot_qq_account

            if _is_image(msg):
                desc = _image_description(msg)
                tag = f"[图片]{desc}" if desc else "[图片]"
                prefix = "我" if is_bot else nickname
                parts.append(f"{prefix}: {tag}")
            else:
                text = _msg_text(msg)
                if (
                    self.per_message_max_chars > 0
                    and text
                    and len(text) > self.per_message_max_chars
                ):
                    text = text[: self.per_message_max_chars] + "..."
                prefix = "我" if is_bot else nickname
                parts.append(f"{prefix}: {text}")

            if is_bot:
                bot_count += 1
            else:
                user_count += 1

        self._stats = {
            "total_messages": len(messages),
            "bot_messages": bot_count,
            "user_messages": user_count,
        }
        return "\n".join(parts)


def weather_by_emotion(messages: List[Dict[str, Any]]) -> str:
    """根据消息文本的关键词频率粗略推断'天气'。"""
    if not messages:
        return random.choice(["晴", "多云", "阴", "多云转晴"])

    content = " ".join(_msg_text(m) for m in messages)
    happy = sum(1 for w in ("哈哈", "笑", "开心", "高兴", "棒", "好", "赞", "爱", "喜欢") if w in content)
    sad = sum(1 for w in ("难过", "伤心", "哭", "痛苦", "失望") if w in content)
    angry = sum(1 for w in ("无语", "醉了", "服了", "烦", "气", "怒") if w in content)
    calm = sum(1 for w in ("平静", "安静", "淡定", "还好", "一般") if w in content)

    if happy >= 2:
        return "晴"
    if happy >= 1:
        return "多云转晴"
    if sad >= 1:
        return "雨"
    if angry >= 1:
        return "阴"
    if calm >= 1:
        return "多云"
    return "多云"
