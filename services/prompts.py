"""Prompt 构建（发说说 / 评论 / 回复 / 回复他人空间的回复）。

模板从 plugin.config 里取，本模块只负责字段填充与拼接。

所有 prompt 在最终输出前会把 ``persona.self_description`` 拼到开头（如非空），
让 LLM 知道"我是谁、长什么样"。
"""

from __future__ import annotations

from typing import Any
from zoneinfo import ZoneInfo
import datetime

from ..utils import get_logger, wrap_untrusted
from .chat_material import build_chat_material

logger = get_logger(__name__)

_SKIP_COMMENT_INSTRUCTION = (
    "\n如果你觉得没必要评论（内容无感、不适合插嘴、太隐私、广告、重复水帖等），"
    "只输出三个字：不回复；否则只输出评论正文。"
)


def _now_str(plugin: Any) -> str:
    return datetime.datetime.now(ZoneInfo(plugin.config.schedule.timezone)).strftime("%Y-%m-%d %H:%M:%S")


def _safe_format(template: str, data: dict) -> str:
    """配置模型已校验占位符，构建失败时明确报告，避免原始模板被发给模型。"""
    try:
        return template.format(**data)
    except (KeyError, IndexError, ValueError) as exc:
        raise ValueError(f"提示词模板无法填充：{exc}") from exc


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
    activity_context = wrap_untrusted(current_activity, label="当前活动")
    data = {
        "current_time": _now_str(plugin),
        "bot_personality": bot_personality,
        "topic": wrap_untrusted(topic, label="说说主题素材"),
        "bot_expression": bot_expression,
        "current_activity": activity_context,
    }
    prompt = _safe_format(template, data)

    if current_activity:
        prompt += "\n说说内容应与当前状态自然相关。"
        prompt += activity_context

    if qzone_api is not None and plugin.config.send.history_number > 0:
        history_num = plugin.config.send.history_number
        prompt += "\n以下是你以前发过的说说，写新说说时注意不要在相隔不长的时间发送相同主题的说说\n"
        try:
            prompt += wrap_untrusted(await qzone_api.get_send_history(history_num), label="历史说说", max_chars=8000)
        except Exception as exc:
            logger.warning("拉历史说说失败: %s", exc)
    material = await build_chat_material(plugin)
    if material:
        prompt += "\n【生活素材】\n" + material
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
    """构造评论说说的 prompt；外来字段只作为背景，rt_con 非空走转发模板。"""
    data = {
        "current_time": _now_str(plugin),
        "created_time": wrap_untrusted(created_time, label="说说发布时间", max_chars=200),
        "bot_personality": bot_personality,
        "bot_expression": bot_expression,
        "target_name": wrap_untrusted(target_name, label="好友昵称", max_chars=500),
        "content": wrap_untrusted(content, label="好友说说正文"),
        "impression": wrap_untrusted(impression if impression is not None else "无印象", label="好友印象"),
    }
    if not rt_con:
        template = plugin.config.read.prompt
    else:
        template = plugin.config.read.rt_prompt
        data["rt_con"] = wrap_untrusted(rt_con, label="转发说说原文")
    prompt = _safe_format(template, data)
    if plugin.config.read.allow_skip_comment:
        # 必须根据配置追加；不能因不可信正文中含“不回复”而省略行为约定。
        prompt += _SKIP_COMMENT_INSTRUCTION
    return _prepend_self(prompt, self_description)


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
        "current_time": _now_str(plugin),
        "created_time": wrap_untrusted(created_time, label="评论发布时间", max_chars=200),
        "bot_personality": bot_personality,
        "bot_expression": bot_expression,
        "nickname": wrap_untrusted(nickname, label="评论者昵称", max_chars=500),
        "content": wrap_untrusted(content, label="说说正文"),
        "comment_content": wrap_untrusted(comment_content, label="好友评论"),
        "impression": wrap_untrusted(impression if impression is not None else "无印象", label="好友印象"),
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
        "current_time": _now_str(plugin),
        "created_time": wrap_untrusted(created_time, label="回复发布时间", max_chars=200),
        "bot_personality": bot_personality,
        "bot_expression": bot_expression,
        "nickname": wrap_untrusted(nickname, label="回复者昵称", max_chars=500),
        "content": wrap_untrusted(content, label="好友说说正文"),
        "bot_comment": wrap_untrusted(bot_comment, label="自己的历史评论"),
        "reply_content": wrap_untrusted(reply_content, label="好友回复"),
        "impression": wrap_untrusted(impression if impression is not None else "无印象", label="好友印象"),
    }
    return _prepend_self(_safe_format(plugin.config.monitor.reply_to_reply_prompt, data), self_description)
