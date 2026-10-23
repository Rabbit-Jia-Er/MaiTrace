"""逐条观察原始消息，处理睡眠、撤回与真实发送确认，不等待或合并连续消息。"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Coroutine, Dict, Optional, Set, Tuple
import asyncio
import json
import random
import re
import time
import uuid

from ...utils import get_logger
from .adapter_compat import component_kind, component_text, group_identity, recall_notice, reply_target_ids, sender_identity, walk_components

if TYPE_CHECKING:
    from ..life.engine import LifeEngine


logger = get_logger(__name__)
PLUGIN_ID = "Rabbit-Jia-Er.MaiTrace"
_TASK_BLOCK = re.compile(r'<plugin_proactive_task\b(?P<attrs>[^>]*)>(?P<body>.*)</plugin_proactive_task>\s*$', re.DOTALL)
_ATTRIBUTE = re.compile(r'([\w.-]+)\s*=\s*"([^"]*)"')
_METADATA = re.compile(r'^附加信息：[ \t]*([^\r\n]+)', re.MULTILINE)
_QUIET = re.compile(r"别回|不用回|不要回|继续睡|别醒|别打扰|安心睡|不用理我")
_WAKE = re.compile(r"醒醒|快醒|叫醒|起床|紧急|救命|出事了|撑不住|危险|报警|急事|轻生|自杀")


@dataclass
class MessageTurn:
    token: str
    key: Tuple[str, str, str]
    created_at: float
    updated_at: float
    source_ids: Set[str] = field(default_factory=set)
    cancelled: bool = False
    wake_candidate: bool = False
    wake_confirmed: bool = False
    group_id: str = ""


@dataclass
class TaskBinding:
    session_id: str
    task_id: str
    claim_token: str
    expires_at: float
    generation: str = ""
    anchor: str = ""
    blocked: bool = False
    direct_reserved: bool = False
    confirmed: bool = False


class MessagingController:
    """所有快速判断只读内存；后台任务归本实例所有，停止时统一收口。"""

    def __init__(self, plugin: Any, *, life_engine: Optional["LifeEngine"]) -> None:
        self.plugin = plugin
        self.life = life_engine
        self._running = False
        self._turns: Dict[str, MessageTurn] = {}
        self._sources: Dict[Tuple[str, str], str] = {}
        self._bindings: Dict[Tuple[str, str], TaskBinding] = {}
        self._anchors: Dict[Tuple[str, str], Tuple[str, str]] = {}
        self._receipts: Dict[str, Dict[str, Any]] = {}
        self._last_inbound_at: Dict[str, float] = {}
        self._tasks: Set[asyncio.Task[Any]] = set()

    async def start(self) -> None:
        self._running = True

    async def stop(self) -> None:
        self._running = False
        for turn in self._turns.values():
            turn.cancelled = True
        tasks = list(self._tasks)
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._tasks.clear()
        self._turns.clear()
        self._sources.clear()
        self._bindings.clear()
        self._anchors.clear()
        self._receipts.clear()
        self._last_inbound_at.clear()

    def _spawn(self, operation: Coroutine[Any, Any, Any]) -> None:
        task = asyncio.create_task(operation, name="maitrace-message-observe")
        self._tasks.add(task)

        def completed(item: asyncio.Task[Any]) -> None:
            self._tasks.discard(item)
            if not item.cancelled():
                error = item.exception()
                if error is not None:
                    logger.error("消息观察任务失败", exc_info=(type(error), error, error.__traceback__))

        task.add_done_callback(completed)

    def _prune(self) -> None:
        cutoff = time.monotonic() - self.plugin.config.messaging.turn_expire_seconds
        expired = {key for key, turn in self._turns.items() if turn.updated_at < cutoff}
        # 仅保留有限的源 ID；等待与批次始终由主程序管理。
        extra = max(0, len(self._turns) - len(expired) - 512)
        expired.update([key for key in self._turns if key not in expired][:extra])
        for key in expired:
            turn = self._turns.pop(key)
            turn.cancelled = True
        self._sources = {key: token for key, token in self._sources.items() if token in self._turns}
        self._bindings = {key: value for key, value in self._bindings.items() if value.expires_at > time.time()}
        self._anchors = {key: value for key, value in self._anchors.items() if (key[0], value[0]) in self._bindings}
        self._receipts = {key: value for key, value in self._receipts.items() if value["expires_at"] > time.monotonic()}
        self._last_inbound_at = {key: value for key, value in self._last_inbound_at.items() if value + self.plugin.config.messaging.turn_expire_seconds > time.time()}

    @staticmethod
    def _text(message: Dict[str, Any]) -> str:
        # 判断用文字不遍历图片 Base64；媒体本体仍按原始顶层段传给 Host。
        text = message.get("processed_plain_text")
        return text if isinstance(text, str) and text else "\n".join(component_text(part) for part in walk_components(message.get("raw_message")) if component_kind(part) == "text")


    def _rest_decision(self, text: str) -> Tuple[bool, bool]:
        config = self.plugin.config.messaging
        if not config.rest_gate_enabled or self.life is None:
            return True, False
        state = self.life.peek_state()
        if state.get("activity_kind") not in {"sleep", "rest"}:
            return True, False
        # 明确勿扰优先于概率和醒后宽限；命令在调用本方法前已独立放行。
        if _QUIET.search(text):
            return False, False
        if float(state.get("awake_grace_until", 0)) > time.time():
            return True, False
        allow = bool(_WAKE.search(text)) or random.random() < config.wake_probability
        return allow, allow

    def _recall(self, message: Dict[str, Any], notice: Dict[str, str]) -> None:
        session = str(message.get("session_id", ""))
        source = notice["recalled_message_id"]
        token = self._sources.get((session, source), "")
        turn = self._turns.get(token)
        if turn is None or turn.key[2] != notice["user_id"]:
            return
        if turn.group_id != notice["group_id"]:
            return
        # 被撤回的源消息只取消本轮尚未发出的内容，不调用平台撤回 Bot 消息。
        turn.cancelled = True

    async def on_receive(self, **kwargs: Any) -> Dict[str, Any]:
        """不等待、不合并、不改写媒体；按真实源消息 ID 登记生活回执。"""
        if not self._running or not self.plugin.config.messaging.enabled:
            return {"action": "continue"}
        message = kwargs.get("message")
        if not isinstance(message, dict):
            raise ValueError("入站 Hook message 必须为对象")
        self._prune()
        notice = recall_notice(message)
        if notice:
            if self.plugin.config.messaging.recall_enabled:
                self._recall(message, notice)
            return {"action": "continue"}
        if message.get("is_notify"):
            return {"action": "continue"}
        platform, session = str(message.get("platform", "")), str(message.get("session_id", ""))
        user_id, display_name = sender_identity(message)
        group_id, _ = group_identity(message)
        message_id = str(message.get("message_id", ""))
        if platform != "qq" or not session or not user_id or not message_id:
            return {"action": "continue"}
        known_private = not group_id and any(
            profile.enabled and profile.user_id == user_id for profile in self.plugin.config.life.profiles
        )
        if known_private and self.life is not None:
            self._last_inbound_at[session] = time.time()
            for binding in self._bindings.values():
                if binding.session_id == session:
                    binding.blocked = True
            for pending in self.life.pending_private_tasks(session):
                in_flight = any(receipt["claim_token"] == pending["claim_token"] for receipt in self._receipts.values())
                # 已提交平台的发送继续等回执，避免新消息让已发送的素材重新进入队列。
                if not in_flight:
                    self._spawn(self.life.release(pending["claim_token"], error="新的真实入站消息已结束主动轮次"))
        text = self._text(message)
        if message.get("is_command") or text.lstrip().startswith("/"):
            return {"action": "continue"}
        if not known_private and not (group_id and self.plugin.config.messaging.recall_enabled):
            return {"action": "continue"}
        if (session, message_id) in self._sources:
            return {"action": "continue"}
        now = time.monotonic()
        turn = MessageTurn(uuid.uuid4().hex, (platform, session, user_id), now, now)
        turn.group_id = group_id
        turn.source_ids.add(message_id)
        self._turns[turn.token] = turn
        self._sources[(session, message_id)] = turn.token
        if known_private and self.life is not None:
            # 仅复制身份和有限文本；媒体原样继续进入主程序，不驻留插件缓存。
            self._spawn(self.life.observe_message(
                user_id=user_id, session_id=session, message_id=message_id,
                text=text[:4000], display_name=display_name, platform=platform,
            ))
        allowed, wake = self._rest_decision(text) if known_private else (True, False)
        turn.wake_candidate = wake
        if not allowed:
            turn.cancelled = True
            return {"action": "abort"}
        return {"action": "continue"}


    def track_planner_task(self, session_id: str, items: Any) -> None:
        """使用当前 Context Item 契约；只接受与本实例候选精确匹配的完整 Host 标记。"""
        if not self._running or not self.plugin.config.messaging.enabled or self.life is None or not isinstance(items, list):
            return
        self._prune()
        for item in items:
            if not isinstance(item, dict) or item.get("item_type") != "UserMessageItem":
                continue
            parts = item.get("parts")
            if not isinstance(parts, list):
                continue
            text = "\n".join(part["text"] for part in parts if isinstance(part, dict) and part.get("type") == "text" and isinstance(part.get("text"), str)).strip()
            match = _TASK_BLOCK.fullmatch(text)
            if match is None:
                continue
            attrs = dict(_ATTRIBUTE.findall(match["attrs"]))
            task_id = attrs.get("id", "")
            if attrs.get("plugin_id") != PLUGIN_ID or not task_id.startswith(f"proactive:{PLUGIN_ID}:"):
                continue
            metadata_lines = _METADATA.findall(match["body"])
            if not metadata_lines:
                continue
            try:
                metadata = json.loads(metadata_lines[-1])
            except json.JSONDecodeError:
                continue
            if not isinstance(metadata, dict) or metadata.get("maitrace_source") != "life":
                continue
            token = str(metadata.get("maitrace_claim_token", ""))
            pending = self.life.private_task(session_id, task_id=task_id, claim_token=token) if token else None
            # Host 必须已经返回并绑定 task_id，不能把用户复制的旧标记当成新任务。
            if pending is None or pending["task_id"] != task_id:
                continue
            already_sent = pending["status"] == "sent"
            retain_until = float(pending["expires_at"]) + (self.plugin.config.messaging.turn_expire_seconds if already_sent else 0)
            if retain_until <= time.time():
                continue
            if float(pending["created_at"]) <= self._last_inbound_at.get(session_id, 0):
                continue
            self._bindings.setdefault((session_id, task_id), TaskBinding(session_id, task_id, token,
                retain_until, blocked=already_sent, confirmed=already_sent))

    def track_reply_anchor(self, session_id: str, reply_message_id: str = "", **kwargs: Any) -> bool:
        """记录一次独立 Replyer 请求；重试和同一次回复的正常分段共用生成标识。"""
        if not self._running or not self.plugin.config.messaging.enabled:
            return True
        self._prune()
        anchor = str(reply_message_id)
        if not anchor:
            return True
        args = kwargs.get("reply_tool_args")
        args = args if isinstance(args, dict) else {}
        explicit_task = str(args.get("maitrace_task_id", ""))
        task_id = explicit_task or anchor
        binding = self._bindings.get((session_id, task_id))
        if binding is not None and explicit_task and args.get("maitrace_claim_token") != binding.claim_token:
            binding = None
        retry = int(kwargs.get("attempt", 1)) > 1 or int(kwargs.get("retry_count", 0)) > 0
        if binding is not None:
            if binding.generation and (not retry or binding.anchor != anchor):
                binding.blocked = True
                return False
            if not binding.generation:
                binding.generation = uuid.uuid4().hex
                binding.anchor = anchor
            self._anchors[(session_id, anchor)] = (binding.task_id, binding.generation)
            return not binding.blocked
        turn = self._turns.get(self._sources.get((session_id, anchor), ""))
        if turn is None:
            return True
        if turn.cancelled:
            return False
        return not turn.cancelled

    def _send_anchor(self, message: Dict[str, Any], kwargs: Dict[str, Any]) -> str:
        explicit = kwargs.get("reply_message_id")
        if isinstance(explicit, str) and explicit:
            return explicit
        targets = reply_target_ids(message)
        return targets[0] if targets else ""

    async def on_send_before(self, **kwargs: Any) -> Dict[str, Any]:
        if not self._running or not self.plugin.config.messaging.enabled:
            return {"action": "continue"}
        message = kwargs.get("message")
        if not isinstance(message, dict):
            raise ValueError("发送 Hook message 必须为对象")
        self._prune()
        session = str(message.get("session_id", ""))
        anchor = self._send_anchor(message, kwargs)
        turn = self._turns.get(self._sources.get((session, anchor), ""))
        task_id, generation = self._anchors.get((session, anchor), (anchor, ""))
        binding = self._bindings.get((session, task_id))
        if binding is None and self.life is not None and self.life.cancelled_private_task(session, task_id):
            return {"action": "abort"}
        if turn is not None:
            if turn.cancelled:
                return {"action": "abort"}
        if binding is not None:
            if binding.blocked or (binding.generation and binding.generation != generation):
                return {"action": "abort"}
            if not binding.generation:
                if binding.direct_reserved:
                    return {"action": "abort"}
                binding.direct_reserved = True
        if turn is None and binding is None:
            return {"action": "continue"}
        # 平台会替换 message_id，使用额外元数据中的随机回执标识承接 after_send。
        receipt_key = uuid.uuid4().hex
        self._receipts[receipt_key] = {"session_id": session, "anchor": anchor,
            "turn_token": turn.token if turn is not None else "",
            "task_id": binding.task_id if binding is not None else "",
            "claim_token": binding.claim_token if binding is not None else "",
            "expires_at": time.monotonic() + self.plugin.config.messaging.turn_expire_seconds}
        modified = dict(kwargs)
        output = deepcopy(message)
        info = output.get("message_info")
        if not isinstance(info, dict):
            raise ValueError("发送消息缺少 message_info")
        additional = info.get("additional_config")
        if not isinstance(additional, dict):
            additional = {}
            info["additional_config"] = additional
        additional["maitrace_send_receipt"] = receipt_key
        modified["message"] = output
        return {"action": "continue", "modified_kwargs": modified}

    async def on_send_after(self, **kwargs: Any) -> Dict[str, Any]:
        if not self._running or self.life is None:
            return {"action": "continue"}
        message = kwargs.get("message")
        if not isinstance(message, dict):
            raise ValueError("发送确认 message 必须为对象")
        info = message.get("message_info")
        additional = info.get("additional_config") if isinstance(info, dict) else None
        receipt_key = str(additional.get("maitrace_send_receipt", "")) if isinstance(additional, dict) else ""
        receipt = self._receipts.pop(receipt_key, None)
        if receipt is None or receipt["session_id"] != message.get("session_id"):
            return {"action": "continue"}
        sent = kwargs.get("sent") is True
        turn = self._turns.get(receipt["turn_token"])
        if sent and turn is not None and turn.wake_candidate and not turn.wake_confirmed:
            turn.wake_confirmed = True
            await self.life.mark_woken(minutes=self.plugin.config.messaging.awake_grace_minutes)
        if receipt["claim_token"]:
            confirmed = await self.life.confirm_private_send(receipt["session_id"], task_id=receipt["task_id"],
                claim_token=receipt["claim_token"], sent=sent, receipt=str(message.get("message_id", "")))
            binding = self._bindings.get((receipt["session_id"], receipt["task_id"]))
            if binding is not None:
                binding.confirmed = binding.confirmed or (sent and confirmed)
                if not sent and not binding.confirmed:
                    binding.blocked = True
        return {"action": "continue"}
