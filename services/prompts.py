"""Prompt 构建（发说说 / 评论 / 回复 / 回复他人空间的回复）。

模板从 plugin.config 里取，本模块只负责字段填充与拼接。

所有 prompt 在最终输出前会把 ``persona.self_description`` 拼到开头（如非空），
让 LLM 知道"我是谁、长什么样"。
"""

from __future__ import annotations

import datetime
import logging
from ..utils import get_logger
from typing import Any, Optional

logger = get_logger(__name__)


def _now_str() -> str:
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _safe_format(template: str, data: dict) -> str:
    """字符串 .format(**data)，缺占位符不报错（自动剔除未提供的键）。"""
    try:
        return template.format(**data)
    except KeyError as exc:
        # 占位符不存在时移除该键再试
        missing = str(exc).strip("'")
        if missing in data:
            data = {k: v for k, v in data.items() if k != missing}
        # 兜底：把所有不存在的占位符填空字符串
        import re
        placeholders = set(re.findall(r"\{(\w+)\}", template))
        for ph in placeholders:
            data.setdefault(ph, "")
        try:
            return template.format(**data)
        except Exception as exc2:
            logger.warning("prompt format 失败: %s", exc2)
            return template


def _prepend_self(prompt: str, self_description: str) -> str:
    """把 self_description 拼到 prompt 开头。空值直接返回原 prompt。"""
    desc = (self_description or "").strip()
    if not desc:
        return prompt
    # 保证以句号结尾，但不重复加
    if not desc.endswith(("。", ".", "！", "!", "？", "?")):
        desc += "。"
    return f"关于你：{desc}\n{prompt}"


async def build_send_prompt(
    plugin,
    topic: str,
    bot_personality: str,
    bot_expression: str,
    *,
    qzone_api=None,
    current_activity: str = "",
    self_description: str = "",
) -> str:
    """构造发说说 prompt。

    qzone_api 不为 None 时会追加近期说说历史，避免重复内容。
    self_description 非空时会拼到 prompt 最开头。
    """
    template = plugin.config.send.prompt
    data = {
        "current_time": _now_str(),
        "bot_personality": bot_personality,
        "topic": topic,
        "bot_expression": bot_expression,
        "current_activity": current_activity,
    }
    prompt = _safe_format(template, data)

    if current_activity:
        prompt += f"\n你当前正在{current_activity}，说说内容应与当前状态自然相关。"

    if qzone_api is not None:
        history_num = plugin.config.send.history_number
        prompt += "\n以下是你以前发过的说说，写新说说时注意不要在相隔不长的时间发送相同主题的说说\n"
        try:
            prompt += await qzone_api.get_send_history(history_num)
        except Exception as exc:
            logger.warning("拉历史说说失败: %s", exc)
    prompt += "\n不要输出多余内容(包括前后缀，冒号和引号，括号()，表情包，at或 @等 )"
    return _prepend_self(prompt, self_description)


def build_comment_prompt(
    plugin,
    target_name: str,
    content: str,
    created_time: str,
    bot_personality: str,
    bot_expression: str,
    impression: Any,
    rt_con: str = "",
    self_description: str = "",
) -> str:
    """构造评论说说的 prompt。rt_con 非空走 rt_prompt 模板。"""
    data = {
        "current_time": _now_str(),
        "created_time": created_time,
        "bot_personality": bot_personality,
        "bot_expression": bot_expression,
        "target_name": target_name,
        "content": content,
        "impression": str(impression) if impression is not None else "无印象",
    }
    if not rt_con:
        template = plugin.config.read.prompt
    else:
        template = plugin.config.read.rt_prompt
        data["rt_con"] = rt_con
    return _prepend_self(_safe_format(template, data), self_description)


def build_reply_prompt(
    plugin,
    nickname: str,
    content: str,
    comment_content: str,
    created_time: str,
    bot_personality: str,
    bot_expression: str,
    impression: Any,
    self_description: str = "",
) -> str:
    """构造回复自己说说下评论的 prompt。"""
    data = {
        "current_time": _now_str(),
        "created_time": created_time,
        "bot_personality": bot_personality,
        "bot_expression": bot_expression,
        "nickname": nickname,
        "content": content,
        "comment_content": comment_content,
        "impression": str(impression) if impression is not None else "无印象",
    }
    return _prepend_self(_safe_format(plugin.config.monitor.reply_prompt, data), self_description)


def build_reply_to_reply_prompt(
    plugin,
    nickname: str,
    content: str,
    bot_comment: str,
    reply_content: str,
    created_time: str,
    bot_personality: str,
    bot_expression: str,
    impression: Any,
    self_description: str = "",
) -> str:
    """构造回复他人空间中对 bot 评论的回复的 prompt。"""
    data = {
        "current_time": _now_str(),
        "created_time": created_time,
        "bot_personality": bot_personality,
        "bot_expression": bot_expression,
        "nickname": nickname,
        "content": content,
        "bot_comment": bot_comment,
        "reply_content": reply_content,
        "impression": str(impression) if impression is not None else "无印象",
    }
    return _prepend_self(_safe_format(plugin.config.monitor.reply_to_reply_prompt, data), self_description)
