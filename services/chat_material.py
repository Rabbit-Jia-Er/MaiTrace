"""从 SDK 查询真实会话的聊天素材，供公开说说使用。"""

from datetime import datetime, timedelta
from typing import Any, Dict, List, Set
from zoneinfo import ZoneInfo
import asyncio
import re

from .llm_runner import LLMRunner
from ..utils import wrap_untrusted

CHAT_QUERY_TIMEOUT_SECONDS = 60.0


class ChatMaterialError(RuntimeError):
    """配置、SDK 查询或摘要失败，调用方不能伪装成没有聊天。"""


def _selected_sessions(streams: List[Dict[str, Any]], config: Any) -> Set[str]:
    selected: Set[str] = set()
    targets = {entry.strip() for entry in config.target_chats if entry.strip()}
    for stream in streams:
        session_id = str(stream["session_id"])
        is_group = stream["chat_type"] == "group"
        if not is_group and not config.include_private_chat_memory:
            continue
        resource = f"group:{stream['group_id']}" if is_group else f"private:{stream['user_id']}"
        matched = resource in targets or f"session:{session_id}" in targets
        if config.filter_mode == "all" or (config.filter_mode == "whitelist" and matched) or (
            config.filter_mode == "blacklist" and not matched
        ):
            selected.add(session_id)
    return selected


def _anonymous_text(messages: List[Dict[str, Any]], limit: int) -> str:
    lines = []
    for message in messages:
        text = str(message.get("processed_plain_text", "")).strip()
        if not text or text.startswith("/"):
            continue
        # 不携带发送者、群名和消息 ID；号码与邮箱也先在规则层隐藏。
        text = re.sub(r"[A-Za-z0-9.+_-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", "[邮箱]", text[:limit])
        # 中文与数字都属于正则的 \w，不能用 \b 判断中文正文里的号码边界。
        text = re.sub(r"(?<!\d)\d{5,}(?!\d)", "[号码]", text)
        lines.append(text)
    return "\n".join(lines)


async def _read_selected_messages(plugin: Any) -> List[Dict[str, Any]]:
    """限制并发并收回在途请求，避免失败后仍有无人持有的 RPC 任务。"""
    config = plugin.config.send
    streams = await plugin.ctx.chat.get_all_streams()
    if not isinstance(streams, list):
        raise ChatMaterialError("主程序未返回聊天流列表")
    selected = _selected_sessions(streams, config)
    if not selected:
        return []
    now = datetime.now(ZoneInfo(plugin.config.schedule.timezone))
    start = now.replace(hour=0, minute=0, second=0, microsecond=0) if config.time_range == "today" else now - timedelta(hours=config.last_hours)
    semaphore = asyncio.Semaphore(4)

    async def read_session(session_id: str) -> List[Dict[str, Any]]:
        async with semaphore:
            items = await plugin.ctx.message.get_by_time_in_chat(
                session_id, start_time=str(start.timestamp()), end_time=str(now.timestamp()),
                limit=config.max_messages, limit_mode="latest", filter_mai=False, filter_command=True,
            )
            if not isinstance(items, list):
                raise ChatMaterialError("主程序未返回消息列表")
            return items

    tasks = [asyncio.create_task(read_session(session_id)) for session_id in sorted(selected)]
    try:
        batches = await asyncio.gather(*tasks)
    finally:
        # gather 首个请求失败时不会自动取消其他请求，必须显式结束并等待它们。
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
    return [item for batch in batches for item in batch
            if isinstance(item, dict) and item.get("session_id") in selected
            and str(item.get("processed_plain_text", "")).strip()
            and not str(item.get("processed_plain_text", "")).lstrip().startswith("/")]


async def build_chat_material(plugin: Any) -> str:
    config = plugin.config.send
    if not config.enable_chat_memory:
        return ""
    try:
        try:
            # 固定查询总预算属于发布链的准备阶段，不随聊天流数量无限增长。
            async with asyncio.timeout(CHAT_QUERY_TIMEOUT_SECONDS):
                chosen = await _read_selected_messages(plugin)
        except TimeoutError as exc:
            raise ChatMaterialError(f"聊天素材查询超时（{CHAT_QUERY_TIMEOUT_SECONDS:g} 秒）") from exc
        chosen.sort(key=lambda item: float(item.get("timestamp", 0)))
        chosen = chosen[-config.max_messages:]
        if len(chosen) < config.min_messages:
            return config.fallback_prompt
        if config.chat_memory_mode == "direct":
            material = _anonymous_text(chosen, config.per_message_max_chars)
        else:
            recent = chosen[-config.hybrid_recent_count:] if config.chat_memory_mode == "hybrid" else []
            earlier = chosen[:-len(recent)] if recent else chosen
            summary = ""
            if earlier:
                logs = wrap_untrusted(_anonymous_text(earlier, config.per_message_max_chars), label="待概括的聊天素材", max_chars=20000)
                prompt = config.summary_prompt.format(chat_logs=logs)
                success, summary = await LLMRunner(
                    plugin.ctx, config.summary_model, timeout=plugin.config.llm.llm_timeout_seconds,
                ).generate(prompt, temperature=0.2, max_tokens=800)
                if not success:
                    raise ChatMaterialError(f"聊天素材摘要失败: {summary}")
            material = "\n".join(part for part in (summary, _anonymous_text(recent, config.per_message_max_chars)) if part)
        return (
            "素材只供匿名概括日常感受，不要公开原话、身份、私人约定或敏感信息。\n"
            + wrap_untrusted(material, label="聊天背景素材", max_chars=12000)
        )
    except ChatMaterialError:
        raise
    except Exception as exc:
        raise ChatMaterialError(f"读取聊天素材失败: {exc}") from exc
