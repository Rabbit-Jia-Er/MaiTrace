"""消息抓取 + 黑/白名单过滤。

通过新 SDK 的 ctx.message.get_by_time / ctx.message.get_by_time_in_chat。
"""

from __future__ import annotations

import logging
from ...utils import get_logger
from typing import Any, Dict, List, Optional, Tuple

from ...utils import peel_envelope

logger = get_logger(__name__)


def _parse_target_config(configs: List[str]) -> Tuple[List[str], List[str]]:
    """解析 target_chats 字符串列表，分离 private QQ 和 group QQ。"""
    private_qqs: List[str] = []
    group_qqs: List[str] = []
    for cfg in configs:
        cfg = cfg.strip()
        if not cfg:
            continue
        if cfg.startswith("private:"):
            private_qqs.append(cfg[len("private:"):].strip())
        elif cfg.startswith("group:"):
            group_qqs.append(cfg[len("group:"):].strip())
        else:
            group_qqs.append(cfg)
    return private_qqs, group_qqs


async def _resolve_session_id(ctx, *, group_id: Optional[str] = None, user_id: Optional[str] = None) -> Optional[str]:
    """通过 ctx.chat.get_stream_by_xxx_id 解析 session_id/stream_id。"""
    try:
        if group_id:
            result = await ctx.chat.get_stream_by_group_id(group_id)
        elif user_id:
            result = await ctx.chat.get_stream_by_user_id(user_id)
        else:
            return None
    except Exception as exc:
        logger.warning("ctx.chat 调用异常: %s", exc)
        return None
    result = peel_envelope(result)
    if not isinstance(result, dict):
        return None
    stream_data = result.get("stream", result)
    if isinstance(stream_data, dict):
        return stream_data.get("session_id") or stream_data.get("stream_id")
    return None


async def _list_messages(
    ctx,
    *,
    start_time: float,
    end_time: float,
    chat_id: str = "",
) -> List[Dict[str, Any]]:
    """统一调 ctx.message.get_by_time(_in_chat)，处理 envelope。"""
    kwargs: Dict[str, Any] = {
        "start_time": str(start_time),
        "end_time": str(end_time),
        "limit": 0,
        "limit_mode": "earliest",
        "filter_mai": False,
        "filter_command": False,
    }
    try:
        if chat_id:
            result = await ctx.message.get_by_time_in_chat(chat_id, **kwargs)
        else:
            kwargs.pop("filter_command", None)  # get_by_time 不接受 filter_command
            result = await ctx.message.get_by_time(**kwargs)
    except Exception as exc:
        logger.error("ctx.message 查询失败 (chat_id=%s): %s", chat_id, exc, exc_info=True)
        return []

    result = peel_envelope(result)
    if isinstance(result, list):
        return [m for m in result if isinstance(m, dict)]
    if isinstance(result, dict):
        if not result.get("success", True):
            logger.warning("ctx.message 返回 success=False: %s", result.get("error"))
            return []
        messages = result.get("messages") or []
        return [m for m in messages if isinstance(m, dict)]
    logger.warning("ctx.message 返回非 list/dict: %s", type(result).__name__)
    return []


class MessageFetcher:
    """按过滤模式抓消息。"""

    def __init__(self, ctx) -> None:
        self._ctx = ctx

    async def fetch_all(self, start_time: float, end_time: float) -> List[Dict[str, Any]]:
        msgs = await _list_messages(self._ctx, start_time=start_time, end_time=end_time, chat_id="")
        msgs.sort(key=lambda m: float(m.get("timestamp", 0) or 0))
        return msgs

    async def fetch_for_chats(
        self,
        chat_ids: List[str],
        start_time: float,
        end_time: float,
    ) -> List[Dict[str, Any]]:
        all_msgs: List[Dict[str, Any]] = []
        for chat_id in chat_ids:
            if not chat_id:
                continue
            msgs = await _list_messages(self._ctx, start_time=start_time, end_time=end_time, chat_id=chat_id)
            all_msgs.extend(msgs)
        all_msgs.sort(key=lambda m: float(m.get("timestamp", 0) or 0))
        return all_msgs

    async def fetch_with_filter(
        self,
        filter_mode: str,
        target_chats: List[str],
        start_time: float,
        end_time: float,
    ) -> List[Dict[str, Any]]:
        """根据 filter_mode 决定取消息策略。

        filter_mode:
            "all" → 全部消息
            "whitelist" → 仅 target_chats 中（空列表则返回空）
            "blacklist" → 排除 target_chats 中（空列表则全部）
        """
        if filter_mode == "all":
            return await self.fetch_all(start_time, end_time)

        if filter_mode == "whitelist":
            if not target_chats:
                logger.info("白名单为空，返回空")
                return []
            session_ids = await self._resolve_session_ids(target_chats)
            if not session_ids:
                logger.warning("白名单解析后为空")
                return []
            return await self.fetch_for_chats(session_ids, start_time, end_time)

        if filter_mode == "blacklist":
            all_msgs = await self.fetch_all(start_time, end_time)
            if not target_chats:
                return all_msgs
            return self._filter_blacklist(all_msgs, target_chats)

        logger.warning("未知 filter_mode=%s", filter_mode)
        return []

    async def _resolve_session_ids(self, configs: List[str]) -> List[str]:
        private_qqs, group_qqs = _parse_target_config(configs)
        session_ids: List[str] = []
        for qq in group_qqs:
            sid = await _resolve_session_id(self._ctx, group_id=qq)
            if sid:
                session_ids.append(sid)
        for qq in private_qqs:
            sid = await _resolve_session_id(self._ctx, user_id=qq)
            if sid:
                session_ids.append(sid)
        return session_ids

    @staticmethod
    def _filter_blacklist(messages: List[Dict[str, Any]], excluded: List[str]) -> List[Dict[str, Any]]:
        ex_private, ex_group = _parse_target_config(excluded)
        ex_p = set(ex_private)
        ex_g = set(ex_group)

        filtered: List[Dict[str, Any]] = []
        for msg in messages:
            info = msg.get("message_info") or {}
            user_info = info.get("user_info") or {}
            group_info = info.get("group_info") or {}
            user_id = str(user_info.get("user_id", "") or "")
            group_id = str(group_info.get("group_id", "") or "")
            if group_id and group_id in ex_g:
                continue
            if not group_id and user_id and user_id in ex_p:
                continue
            filtered.append(msg)
        return filtered

    @staticmethod
    def filter_min_messages_per_chat(
        messages: List[Dict[str, Any]],
        min_per_chat: int,
    ) -> List[Dict[str, Any]]:
        """剔除单聊消息数 < min_per_chat 的聊天。"""
        if min_per_chat <= 0 or not messages:
            return messages
        by_chat: Dict[str, List[Dict[str, Any]]] = {}
        for msg in messages:
            sid = str(msg.get("session_id", "") or "")
            by_chat.setdefault(sid, []).append(msg)
        out: List[Dict[str, Any]] = []
        for sid, msgs in by_chat.items():
            if len(msgs) >= min_per_chat:
                out.extend(msgs)
        out.sort(key=lambda m: float(m.get("timestamp", 0) or 0))
        return out
