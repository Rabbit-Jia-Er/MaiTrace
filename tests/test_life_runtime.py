"""生活/素材/消息的离线行为回归；真实 SDK 代理只连接内存 RPC。"""

from datetime import datetime, timedelta
from importlib.util import find_spec
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from typing import Any, Dict, List
from unittest import IsolatedAsyncioTestCase, TestCase, main
from unittest.mock import AsyncMock, patch
from zoneinfo import ZoneInfo
import asyncio
import json
import sys
import threading
import time


sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from maibot_sdk.context import PluginContext

from MaiTrace.config import MaiTracePluginConfig
from MaiTrace.services.activity import ActivityInfo, ActivityType
from MaiTrace.services.life import LifeEngine, LifeStore
from MaiTrace.services.life.config import ImportantDate, LifeUserProfile
from MaiTrace.services.life.models import LifeState, generate_object, json_object
from MaiTrace.services.life.state import LifeStateEngine
from MaiTrace.services.llm_runner import LLMRunner
from MaiTrace.services.messaging import MessagingController
from MaiTrace.services.messaging.controller import PLUGIN_ID
from MaiTrace.services.routine import RoutineRunner


class FakeRPC:
    def __init__(self) -> None:
        self.calls: List[Dict[str, Any]] = []
        self.private_streams = {user_id: {"session_id": f"private-{user_id}", "chat_type": "private", "platform": "qq",
            "user_id": user_id, "user_nickname": f"用户{user_id}", "group_id": ""} for user_id in ("111", "222", "333")}
        self.group_streams = {"701": {"session_id": "group-701", "chat_type": "group", "platform": "qq", "group_id": "701", "group_name": "测试群"}}

    async def __call__(self, method: str, plugin_id: str, payload: Dict[str, Any], **kwargs: Any) -> Any:
        if method != "cap.call" or plugin_id != PLUGIN_ID:
            raise AssertionError("不应绕过 SDK capability")
        self.calls.append(payload)
        capability, args = payload["capability"], payload["args"]
        if capability == "chat.get_stream_by_user_id":
            return {"success": True, "stream": self.private_streams.get(args["user_id"])}
        if capability == "chat.get_all_streams":
            return {"success": True, "streams": [*self.private_streams.values(), *self.group_streams.values()]}
        if capability == "chat.get_stream_by_group_id":
            return {"success": True, "stream": self.group_streams.get(args["group_id"])}
        if capability == "maisaka.proactive.trigger":
            return {"success": True, "queued": True, "stream_id": args["stream_id"], "task_id": f"proactive:{PLUGIN_ID}:{len(self.calls)}"}
        raise AssertionError(f"测试没有授权此能力：{capability}")


class FakeGenerator:
    def __init__(self) -> None:
        self.responses: List[Any] = []
        self.calls: List[str] = []

    async def generate(self, prompt: str, **kwargs: Any) -> Any:
        self.calls.append(prompt)
        if not self.responses:
            raise AssertionError("测试未提供模型结果，禁止真实请求")
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        if isinstance(response, dict):
            return True, json.dumps(response, ensure_ascii=False)
        return response


def incoming(message_id: str, *, user_id: str = "111", session: str = "private-111", group_id: str = "", text: str = "你好") -> Dict[str, Any]:
    return {"message_id": message_id, "session_id": session, "platform": "qq", "timestamp": str(time.time()),
            "message_info": {"user_info": {"user_id": user_id, "user_nickname": "测试用户"},
                "group_info": {"group_id": group_id, "group_name": "测试群"} if group_id else None, "additional_config": {}},
            "raw_message": [{"type": "text", "data": text}], "processed_plain_text": text}


def outgoing(anchor: str, *, session: str = "private-111", message_id: str = "send-original") -> Dict[str, Any]:
    message = incoming(message_id, user_id="999", session=session, text="麦麦的回复")
    message["reply_to"] = anchor
    return message


def task_item(task_id: str, token: str, *, item_type: str = "UserMessageItem") -> Dict[str, Any]:
    metadata = json.dumps({"maitrace_source": "life", "maitrace_claim_token": token}, ensure_ascii=False)
    return {"item_type": item_type,
            "meta": {"item_id": "ctx-random-not-task-id", "logical_turn_id": None, "timestamp": "2026-09-11T10:00:00+08:00"},
            "parts": [{"type": "text", "text": f'<plugin_proactive_task id="{task_id}" plugin_id="{PLUGIN_ID}">\n'
                      f'插件请求你主动处理一轮聊天：maitrace_life\n附加信息：{metadata}\n'
                      '请结合当前聊天关系、记忆和上下文，自行决定是否回复以及如何表达。\n</plugin_proactive_task>'}]}


class LifeCase(IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temp = TemporaryDirectory(prefix="maitrace-life-")
        config = MaiTracePluginConfig()
        config.environment.calendar_enabled = False
        config.life.scene_enabled = False
        config.life.dream_enabled = False
        config.life.diary_enabled = False
        config.plugin.admin_qq = ["111", "222", "333"]
        config.life.profiles = [LifeUserProfile(user_id="111", role="owner", proactive_enabled=True, quiet_start="00:00", quiet_end="00:00"),
                                LifeUserProfile(user_id="222", role="friend", proactive_enabled=True, quiet_start="00:00", quiet_end="00:00")]
        config.messaging.enabled = True
        self.rpc, self.generator = FakeRPC(), FakeGenerator()
        self.plugin = SimpleNamespace(config=config, ctx=PluginContext(PLUGIN_ID, rpc_call=self.rpc), _messaging=None)
        self.engine = LifeEngine(self.plugin, data_dir=Path(self.temp.name), generator=self.generator)
        await self.engine.start()
        self.controller = MessagingController(self.plugin, life_engine=self.engine)
        self.plugin._messaging = self.controller
        await self.controller.start()

    async def asyncTearDown(self) -> None:
        await self.controller.stop()
        await self.engine.stop()
        self.temp.cleanup()

    async def opportunity(self, source_key: str, privacy: str = "public", **kwargs: Any) -> str:
        now = time.time()
        return await self.engine.store.add_opportunity(source="test", source_key=source_key, topic="一段安静的午后", motive="想分享", privacy=privacy,
            now=now, expires_at=now + 7200, **kwargs)

    async def drain_observers(self) -> None:
        if self.controller._tasks:
            await asyncio.gather(*list(self.controller._tasks))

    async def private_claim(self) -> Dict[str, Any]:
        await self.opportunity("private", "owner_only")
        self.plugin.config.life.private_proactive_enabled = True
        self.assertTrue(await self.engine.patrol_private())
        request = next(call["args"] for call in reversed(self.rpc.calls) if call["capability"] == "maisaka.proactive.trigger")
        token = request["metadata"]["maitrace_claim_token"]
        return self.engine.private_task(request["stream_id"], claim_token=token)

    async def test_public_claim_is_exclusive_and_filters_private_material(self) -> None:
        await self.opportunity("diary", "owner_only", weight=1.0)
        await self.opportunity("date", "target_only", target_user_id="222", weight=1.0)
        public_id = await self.opportunity("scene", weight=0.1)
        claims = await asyncio.gather(self.engine.claim_public_opportunity(), self.engine.claim_public_opportunity())
        valid = [item for item in claims if item is not None]
        self.assertEqual(len(valid), 1)
        self.assertEqual(valid[0]["id"], public_id)
        self.assertEqual(valid[0]["classification"], "normal")
        self.assertTrue(valid[0]["instruction"])


    async def test_cross_store_claims_are_atomic(self) -> None:
        second = LifeStore(Path(self.temp.name))
        await second.initialize()
        try:
            await self.opportunity("shared")
            results = await asyncio.gather(
                self.engine.store.claim_public("a", time.time(), 30),
                second.claim_public("b", time.time(), 30),
            )
            self.assertEqual(sum(result is not None for result in results), 1)
        finally:
            await second.close()

    async def test_expired_lease_cannot_confirm_or_release_new_owner(self) -> None:
        await self.opportunity("lease")
        now = time.time()
        first = await self.engine.store.claim_public("one", now, 1)
        second = await self.engine.store.claim_public("two", now + 2, 30)
        self.assertNotEqual(first["claim_token"], second["claim_token"])
        self.assertFalse(await self.engine.store.confirm(first["claim_token"], now=now + 2, day="2026-09-11"))
        self.assertFalse(await self.engine.release(first["claim_token"]))
        self.assertFalse(await self.engine.quarantine(first["claim_token"], error="旧发布结果迟到"))
        self.assertTrue(await self.engine.store.confirm(second["claim_token"], now=now + 3, day="2026-09-11"))

    async def test_routine_late_success_after_lease_consumes_real_store_material(self) -> None:
        # 本例验证迟到回执结算，不受执行测试时的深夜静默时段影响。
        self.plugin.config.routine.respect_silent_hours = False
        opportunity_id = await self.opportunity("late-qzone")
        routine = RoutineRunner(SimpleNamespace(config=self.plugin.config, life_engine=self.engine))
        clock = [self.engine.environment.now()]

        async def publish(*args: Any, **kwargs: Any) -> Any:
            # 仅替换外部发布；Routine 的认领、确认及 finally 使用真实 LifeEngine/SQLite。
            rows = await self.engine.store.list_opportunities()
            current = next(row for row in rows if row["id"] == opportunity_id)
            clock[0] = datetime.fromtimestamp(current["lease_until"] + 1, tz=clock[0].tzinfo)
            kwargs["publication"].stage = "confirmed"
            kwargs["publication"].tid = "late-qzone-receipt"
            return True, "已实际发布的正文"

        with patch.object(routine, "_llm_decide", new=AsyncMock(return_value=True)), \
                patch("MaiTrace.services.routine.send_feed", side_effect=publish), \
                patch.object(self.engine.environment, "now", side_effect=lambda: clock[0]):
            await routine._try_post_feed(ActivityInfo(ActivityType.RELAXING, "休息"))
        current = next(row for row in await self.engine.store.list_opportunities() if row["id"] == opportunity_id)
        self.assertEqual(current["status"], "consumed")
        self.assertIsNone(await self.engine.store.claim_public("other", clock[0].timestamp(), 30))

    async def test_reaped_private_lease_accepts_only_exact_late_receipt_once(self) -> None:
        pending = await self.private_claim()
        future = float(pending["expires_at"]) + 1
        # 空间消费者只能触发全局到期回收，不能认领 owner_only 素材。
        self.assertIsNone(await self.engine.store.claim_public("qzone", future, 30))
        await self.engine.store.cleanup(future, 30)
        clock = datetime.fromtimestamp(future, tz=self.engine.environment.now().tzinfo)
        with patch.object(self.engine.environment, "now", return_value=clock):
            self.assertFalse(await self.engine.confirm_private_send("private-222", task_id=pending["task_id"], claim_token=pending["claim_token"]))
            self.assertFalse(await self.engine.confirm_private_send("private-111", task_id="wrong-task", claim_token=pending["claim_token"]))
            self.assertFalse(await self.engine.confirm_private_send("private-111", task_id=pending["task_id"], claim_token="wrong-token"))
            for _ in range(2):
                self.assertTrue(await self.engine.confirm_private_send("private-111", task_id=pending["task_id"], claim_token=pending["claim_token"], receipt="late-private-receipt"))
        owner = next(user for user in await self.engine.store.users() if user["user_id"] == "111")
        self.assertEqual(owner["proactive_count"], 1)
        self.assertEqual(owner["proactive_day"], clock.date().isoformat())
        self.assertEqual((await self.engine.store.list_opportunities(public_only=False))[0]["status"], "consumed")

    async def test_reaped_private_lease_can_be_quarantined_without_reopening(self) -> None:
        pending = await self.private_claim()
        future = float(pending["expires_at"]) + 1
        self.assertIsNone(await self.engine.store.claim_public("qzone", future, 30))
        self.assertTrue(await self.engine.quarantine(pending["claim_token"], error="平台结果仍待核对"))
        self.assertFalse(await self.engine.release(pending["claim_token"]))
        self.assertFalse(await self.engine.store.confirm(pending["claim_token"], now=future, day="2026-09-11"))
        await self.engine.store.cleanup(future + 400 * 86400, 1)
        owner = next(user for user in await self.engine.store.users() if user["user_id"] == "111")
        self.assertIsNone(await self.engine.store.claim_private(owner, future + 400 * 86400, 30))
        self.assertEqual((await self.engine.store.management_counts())["uncertain"], 1)

    async def test_restart_without_cached_task_accepts_expired_private_receipt_once(self) -> None:
        pending = await self.private_claim()
        future = float(pending["expires_at"]) + 1
        self.assertIsNone(await self.engine.store.claim_public("qzone", future, 30))
        await self.engine.store.cleanup(future, 30)
        clock = datetime.fromtimestamp(future, tz=self.engine.environment.now().tzinfo)
        await self.controller.stop()
        await self.engine.stop()
        self.engine = LifeEngine(self.plugin, data_dir=Path(self.temp.name), generator=self.generator)
        with patch.object(self.engine.environment, "now", return_value=clock):
            await self.engine.start()
            self.assertEqual(self.engine._private_tasks, {})
            self.assertEqual(await self.engine.store.private_claim_for_task("private-111", pending["task_id"]), pending["claim_token"])
            self.assertFalse(await self.engine.confirm_private_send("private-222", task_id=pending["task_id"], claim_token=pending["claim_token"]))
            self.assertFalse(await self.engine.confirm_private_send("private-111", task_id="unknown-task", claim_token=pending["claim_token"]))
            self.assertFalse(await self.engine.confirm_private_send("private-111", task_id=pending["task_id"], claim_token="wrong-token"))
            # 仅 task/session 的查询和显式 token 均走真实库；sent 再查询也保持准确且只扣一次。
            self.assertTrue(await self.engine.confirm_private_send("private-111", task_id=pending["task_id"], receipt="restart-late-receipt"))
            self.assertEqual(await self.engine.store.private_claim_for_task("private-111", pending["task_id"]), pending["claim_token"])
            self.assertTrue(await self.engine.confirm_private_send("private-111", task_id=pending["task_id"], claim_token=pending["claim_token"], receipt="duplicate-receipt"))
            self.assertFalse(await self.engine.confirm_private_send("private-222", task_id=pending["task_id"], claim_token=pending["claim_token"]))
        self.assertEqual(self.engine._private_tasks, {})
        owner = next(user for user in await self.engine.store.users() if user["user_id"] == "111")
        self.assertEqual(owner["proactive_count"], 1)
        self.assertEqual(owner["proactive_day"], clock.date().isoformat())

    async def test_uncached_task_lookup_rejects_previous_owner_after_reclaim(self) -> None:
        pending = await self.private_claim()
        future = float(pending["expires_at"]) + 1
        owner = next(user for user in await self.engine.store.users() if user["user_id"] == "111")
        replacement = await self.engine.store.claim_private(owner, future, 30)
        replacement_task = pending["task_id"] + ":replacement"
        await self.engine.store.bind_private_task(replacement["claim_token"], replacement_task)
        await self.engine.store.cleanup(future, 30)
        self.engine._private_tasks.clear()
        clock = datetime.fromtimestamp(future, tz=self.engine.environment.now().tzinfo)
        with patch.object(self.engine.environment, "now", return_value=clock):
            self.assertEqual(await self.engine.store.private_claim_for_task("private-111", pending["task_id"]), "")
            self.assertFalse(await self.engine.confirm_private_send("private-111", task_id=pending["task_id"], claim_token=pending["claim_token"]))
            self.assertFalse(await self.engine.confirm_private_send("private-111", task_id=pending["task_id"], claim_token=replacement["claim_token"]))
            self.assertEqual(await self.engine.store.private_claim_for_task("private-111", replacement_task), replacement["claim_token"])
            self.assertTrue(await self.engine.confirm_private_send("private-111", task_id=replacement_task, receipt="new-owner-receipt"))
            self.assertEqual(await self.engine.store.private_claim_for_task("private-111", pending["task_id"]), "")

    async def test_uncached_task_lookup_rejects_release_quarantine_and_unknown(self) -> None:
        owner = next(user for user in await self.engine.store.users() if user["user_id"] == "111")
        owner["stream_id"] = self.rpc.private_streams["111"]["session_id"]
        for terminal in ("release", "quarantine"):
            with self.subTest(terminal=terminal):
                await self.opportunity("uncached-" + terminal, "owner_only")
                claim = await self.engine.store.claim_private(owner, time.time(), 30)
                task_id = f"proactive:{PLUGIN_ID}:{terminal}"
                await self.engine.store.bind_private_task(claim["claim_token"], task_id)
                if terminal == "release":
                    self.assertTrue(await self.engine.release(claim["claim_token"], error="明确未发送"))
                else:
                    self.assertTrue(await self.engine.quarantine(claim["claim_token"], error="平台结果待核对"))
                self.assertEqual(self.engine._private_tasks, {})
                self.assertEqual(await self.engine.store.private_claim_for_task("private-111", task_id), "")
                self.assertFalse(await self.engine.confirm_private_send("private-111", task_id=task_id, claim_token=claim["claim_token"]))
        self.assertEqual(await self.engine.store.private_claim_for_task("private-111", "unknown-task"), "")
        self.assertEqual(await self.engine.store.private_claim_for_task("", "unknown-task"), "")
        owner = next(user for user in await self.engine.store.users() if user["user_id"] == "111")
        self.assertEqual(owner["proactive_count"], 0)

    async def test_reaped_private_lease_explicit_release_rejects_late_receipt(self) -> None:
        pending = await self.private_claim()
        future = float(pending["expires_at"]) + 1
        self.assertIsNone(await self.engine.store.claim_public("qzone", future, 30))
        self.assertTrue(await self.engine.release(pending["claim_token"], error="明确未发送"))
        self.assertFalse(await self.engine.confirm_private_send("private-111", task_id=pending["task_id"], claim_token=pending["claim_token"]))
        owner = next(user for user in await self.engine.store.users() if user["user_id"] == "111")
        replacement = await self.engine.store.claim_private(owner, future + 1, 30)
        self.assertNotEqual(replacement["claim_token"], pending["claim_token"])
        self.assertFalse(await self.engine.quarantine(pending["claim_token"], error="旧调用结果"))
        self.assertFalse(await self.engine.store.confirm(pending["claim_token"], now=future + 1, day="2026-09-11"))

    async def test_late_lease_confirmation_and_reclaim_are_atomic_across_connections(self) -> None:
        await self.opportunity("late-race")
        now = time.time()
        first = await self.engine.store.claim_public("one", now, 1)
        second = LifeStore(Path(self.temp.name))
        await second.initialize()
        try:
            confirmed, replacement = await asyncio.gather(
                self.engine.store.confirm(first["claim_token"], now=now + 2, day="2026-09-11"),
                second.claim_public("two", now + 2, 30),
            )
            if confirmed:
                self.assertIsNone(replacement)
                self.assertEqual((await second.list_opportunities())[0]["status"], "consumed")
            else:
                self.assertIsNotNone(replacement)
                self.assertNotEqual(replacement["claim_token"], first["claim_token"])
                self.assertFalse(await self.engine.quarantine(first["claim_token"], error="旧调用结果"))
                self.assertTrue(await second.confirm(replacement["claim_token"], now=now + 3, day="2026-09-11"))
        finally:
            await second.close()

    async def test_uncertain_result_is_never_released_or_reclaimed(self) -> None:
        await self.opportunity("uncertain")
        claim = await self.engine.claim_public_opportunity()
        self.assertTrue(await self.engine.quarantine(claim["claim_token"], error="POST 后连接断开"))
        self.assertFalse(await self.engine.release(claim["claim_token"]))
        self.assertFalse(await self.engine.confirm(claim["claim_token"]))
        await self.engine.store.cleanup(time.time() + 400 * 86400, 1)
        self.assertIsNone(await self.engine.store.claim_public("new", time.time() + 4000, 30))
        self.assertEqual((await self.engine.store.management_counts())["uncertain"], 1)

    async def test_owner_only_target_does_not_become_friend_material(self) -> None:
        await self.opportunity("former-owner", "owner_only", target_user_id="222")
        friend = next(user for user in await self.engine.store.users() if user["user_id"] == "222")
        self.assertIsNone(await self.engine.store.claim_private(friend, time.time(), 30))

    async def test_host_acceptance_does_not_consume_private_quota(self) -> None:
        pending = await self.private_claim()
        users = await self.engine.store.users()
        self.assertEqual(next(user for user in users if user["user_id"] == "111")["proactive_count"], 0)
        self.assertFalse(await self.engine.confirm_private_send("private-222", task_id=pending["task_id"], claim_token=pending["claim_token"]))
        self.assertTrue(await self.engine.confirm_private_send("private-111", task_id=pending["task_id"], claim_token=pending["claim_token"]))
        self.assertTrue(await self.engine.confirm_private_send("private-111", task_id=pending["task_id"], claim_token=pending["claim_token"]))
        self.assertEqual(next(user for user in await self.engine.store.users() if user["user_id"] == "111")["proactive_count"], 1)

    async def test_planner_items_and_real_receipt_confirm_once(self) -> None:
        pending = await self.private_claim()
        task_id, token = pending["task_id"], pending["claim_token"]
        self.controller.track_planner_task("private-111", [task_item(task_id, "forged-token")])
        self.assertFalse(self.controller._bindings)
        self.controller.track_planner_task("private-111", [task_item(task_id, token, item_type="SystemMessageItem")])
        self.assertFalse(self.controller._bindings)
        self.controller.track_planner_task("private-111", [task_item(task_id, token)])
        self.assertTrue(self.controller.track_reply_anchor("private-111", task_id, attempt=1, retry_count=0))
        before = await self.controller.on_send_before(message=outgoing(task_id), reply_message_id=task_id)
        message = before["modified_kwargs"]["message"]
        message["message_id"] = "platform-receipt-123"
        await self.controller.on_send_after(message=message, sent=True, reply_message_id=task_id)
        # 同一 Replyer 的后续段仍放行，quota 的幂等性不依赖消息 ID 相同。
        follow = await self.controller.on_send_before(message=outgoing(task_id, message_id="segment2"), reply_message_id=task_id)
        self.assertEqual(follow["action"], "continue")
        await self.controller.on_send_after(message=follow["modified_kwargs"]["message"], sent=True)
        user = next(user for user in await self.engine.store.users() if user["user_id"] == "111")
        self.assertEqual(user["proactive_count"], 1)
        self.assertFalse(self.controller.track_reply_anchor("private-111", task_id, attempt=1))
        self.assertEqual((await self.controller.on_send_before(message=outgoing(task_id), reply_message_id=task_id))["action"], "abort")

    async def test_arbitrary_reply_in_same_session_never_consumes_proactive(self) -> None:
        pending = await self.private_claim()
        self.controller.track_planner_task("private-111", [task_item(pending["task_id"], pending["claim_token"])])
        self.controller.track_reply_anchor("private-111", "ordinary-user-message", attempt=1)
        before = await self.controller.on_send_before(message=outgoing("ordinary-user-message"))
        self.assertNotIn("modified_kwargs", before)
        await self.controller.on_send_after(message=outgoing("ordinary-user-message"), sent=True)
        self.assertEqual(next(user for user in await self.engine.store.users() if user["user_id"] == "111")["proactive_count"], 0)

    async def test_failed_send_releases_private_material_without_quota(self) -> None:
        pending = await self.private_claim()
        self.controller.track_planner_task("private-111", [task_item(pending["task_id"], pending["claim_token"])])
        before = await self.controller.on_send_before(message=outgoing(pending["task_id"]), reply_message_id=pending["task_id"])
        await self.controller.on_send_after(message=before["modified_kwargs"]["message"], sent=False)
        user = next(user for user in await self.engine.store.users() if user["user_id"] == "111")
        self.assertEqual(user["proactive_count"], 0)
        self.assertEqual((await self.engine.store.list_opportunities(public_only=False))[0]["status"], "available")

    async def test_restart_recovers_pending_and_sent_tombstones(self) -> None:
        pending = await self.private_claim()
        await self.controller.stop()
        await self.engine.stop()
        self.engine = LifeEngine(self.plugin, data_dir=Path(self.temp.name), generator=self.generator)
        await self.engine.start()
        self.controller = MessagingController(self.plugin, life_engine=self.engine)
        self.plugin._messaging = self.controller
        await self.controller.start()
        self.controller.track_planner_task("private-111", [task_item(pending["task_id"], pending["claim_token"])])
        before = await self.controller.on_send_before(message=outgoing(pending["task_id"]))
        await self.controller.on_send_after(message=before["modified_kwargs"]["message"], sent=True)
        await self.controller.stop()
        # 同一进程热重载消息控制器后，从已确认任务恢复防重复墓碑。
        self.controller = MessagingController(self.plugin, life_engine=self.engine)
        self.plugin._messaging = self.controller
        await self.controller.start()
        self.controller.track_planner_task("private-111", [task_item(pending["task_id"], pending["claim_token"])])
        self.assertFalse(self.controller.track_reply_anchor("private-111", pending["task_id"], attempt=1))
        self.assertEqual((await self.controller.on_send_before(message=outgoing(pending["task_id"])))["action"], "abort")

    async def test_new_inbound_cancels_unsent_proactive_but_settles_inflight_receipt(self) -> None:
        pending = await self.private_claim()
        self.controller.track_planner_task("private-111", [task_item(pending["task_id"], pending["claim_token"])])
        before = await self.controller.on_send_before(message=outgoing(pending["task_id"]))
        await self.controller.on_receive(message=incoming("new-user-message"))
        await self.drain_observers()
        self.assertEqual((await self.engine.store.list_opportunities(public_only=False))[0]["status"], "claimed")
        await self.controller.on_send_after(message=before["modified_kwargs"]["message"], sent=True)
        self.assertEqual(next(user for user in await self.engine.store.users() if user["user_id"] == "111")["proactive_count"], 1)
        self.assertEqual((await self.controller.on_send_before(message=outgoing(pending["task_id"])))["action"], "abort")

    async def test_new_inbound_prevents_old_marker_reactivation(self) -> None:
        pending = await self.private_claim()
        await self.controller.on_receive(message=incoming("new-turn"))
        await self.drain_observers()
        self.controller.track_planner_task("private-111", [task_item(pending["task_id"], pending["claim_token"])])
        self.assertFalse(self.controller._bindings)
        self.assertEqual((await self.controller.on_send_before(message=outgoing(pending["task_id"])))["action"], "abort")


    async def test_original_messages_pass_immediately_without_retaining_media(self) -> None:
        first = incoming("a", text="先看这张图")
        first["raw_message"].append({"type": "image", "data": {"base64": "test-image"}})
        first["is_picture"] = True
        second = incoming("b", text="再看文字")
        originals = json.dumps([first, second], ensure_ascii=False)
        results = await asyncio.gather(
            self.controller.on_receive(message=first),
            self.controller.on_receive(message=second),
        )
        self.assertEqual(results, [{"action": "continue"}, {"action": "continue"}])
        self.assertEqual(json.dumps([first, second], ensure_ascii=False), originals)
        self.assertNotIn("test-image", repr(self.controller._turns))
        # 重复投递仍交由 Host 判断，但不重复写入生活观察记录。
        self.assertEqual(await self.controller.on_receive(message=first), {"action": "continue"})
        await self.drain_observers()
        self.assertEqual((await self.engine.store.interaction_counts(0, time.time() + 1))["total"], 2)

    async def test_ordinary_replies_remain_host_controlled(self) -> None:
        await self.controller.on_receive(message=incoming("ordinary"))
        self.assertTrue(self.controller.track_reply_anchor("private-111", "ordinary", attempt=1))
        before = await self.controller.on_send_before(message=outgoing("ordinary"))
        await self.controller.on_send_after(message=before["modified_kwargs"]["message"], sent=True)
        self.assertTrue(self.controller.track_reply_anchor("private-111", "ordinary", attempt=1))
        self.assertEqual((await self.controller.on_send_before(message=outgoing("ordinary")))["action"], "continue")
        self.assertEqual(next(user for user in await self.engine.store.users() if user["user_id"] == "111")["proactive_count"], 0)


    async def test_group_messages_pass_without_private_observation(self) -> None:
        requests = [
            incoming("g1-u1", user_id="111", session="g1", group_id="701", text="甲"),
            incoming("g1-u2", user_id="222", session="g1", group_id="701", text="乙"),
            incoming("g2-u1", user_id="111", session="g2", group_id="702", text="丙"),
        ]
        results = await asyncio.gather(*(self.controller.on_receive(message=request) for request in requests))
        self.assertEqual(results, [{"action": "continue"}] * 3)
        self.assertEqual(len(self.controller._turns), 3)
        await self.drain_observers()
        self.assertEqual((await self.engine.store.interaction_counts(0, time.time() + 1))["total"], 0)
        self.assertIsNone(self.engine.user_for_session("g1"))

    async def test_recall_cancels_only_matching_source_owner(self) -> None:
        task = asyncio.create_task(self.controller.on_receive(message=incoming("source")))
        await asyncio.sleep(0.005)
        notice = incoming("recall")
        notice["is_notify"] = True
        notice["message_info"]["additional_config"] = {"snowluma_notice_type": "friend_recall", "snowluma_notice_payload": {"message_id": "source", "user_id": "222"}}
        await self.controller.on_receive(message=notice)
        self.assertEqual((await task)["action"], "continue")
        notice["message_info"]["additional_config"]["snowluma_notice_payload"]["user_id"] = "111"
        await self.controller.on_receive(message=notice)
        self.assertEqual((await self.controller.on_send_before(message=outgoing("source")))["action"], "abort")
        self.assertFalse(any(call["capability"].startswith("send.") for call in self.rpc.calls))

    async def test_wake_requires_matching_success_and_quiet_precedes_probability(self) -> None:
        self.plugin.config.messaging.rest_gate_enabled = True
        self.plugin.config.messaging.wake_probability = 1.0
        state = await self.engine.store.get_state()
        state.activity_kind, state.sleep_phase = "sleep", "deep_sleep"
        await self.engine.store.save_state(state)
        await self.engine._refresh_views(self.engine.environment.now())
        result = await self.controller.on_receive(message=incoming("quiet", text="继续睡，不用回"))
        self.assertEqual(result["action"], "abort")
        await self.controller.on_receive(message=incoming("wake", text="醒醒，有急事"))
        self.assertEqual(self.engine.peek_state()["sleep_phase"], "deep_sleep")
        before = await self.controller.on_send_before(message=outgoing("wake"))
        await self.controller.on_send_after(message=before["modified_kwargs"]["message"], sent=False)
        self.assertEqual(self.engine.peek_state()["sleep_phase"], "deep_sleep")
        await self.controller.on_receive(message=incoming("wake2", text="醒醒"))
        before = await self.controller.on_send_before(message=outgoing("wake2"))
        await self.controller.on_send_after(message=before["modified_kwargs"]["message"], sent=True)
        self.assertEqual(self.engine.peek_state()["sleep_phase"], "woken")


    async def test_missing_diary_model_result_is_not_saved_as_fake_diary(self) -> None:
        self.plugin.config.life.diary_enabled = True
        self.plugin.config.life.diary_hour = 0
        now = self.engine.environment.now()
        yesterday = (now - timedelta(days=1)).date().isoformat()
        await self.engine.store.put_scene("past-scene", yesterday, "读书", "翻过一本书", now.timestamp() - 86400)
        self.generator.responses.append((False, "模型不可用"))
        with self.assertRaisesRegex(RuntimeError, "模型不可用"):
            await self.engine.memory.diary(now)
        self.assertEqual(await self.engine.store.memories("diary"), [])

    async def test_life_materials_allow_reasoning_before_json(self) -> None:
        self.plugin.config.life.scene_enabled = True
        self.plugin.config.life.dream_enabled = True
        self.plugin.config.life.diary_enabled = True
        self.plugin.config.life.diary_hour = 0
        now = self.engine.environment.now()
        yesterday = (now - timedelta(days=1)).date().isoformat()
        await self.engine.advance({"has_activity": True, "activity": {"name": "阅读", "description": "翻书",
            "goal_type": "study", "time_window": "00:00-24:00"}}, now=now)
        await self.engine.store.put_scene("past-scene", yesterday, "阅读", "读了一本书", now.timestamp() - 86400)
        await self.engine.store.save_memory(key="wake", kind="wake_event", day=yesterday,
            payload={"hours": 8}, now=now.timestamp())
        responses = iter([
            {"scene": "安静地翻页", "topic": "读书的午后", "motive": "想分享", "shareable": True},
            {"summary": "梦见走过一条安静的小路", "fragments": ["小路"]},
            {"title": "读书的一天", "content": "昨天读了一本书。", "mood_summary": "平静"},
        ])

        async def reasoning_rpc(method: str, plugin_id: str, payload: Dict[str, Any], **kwargs: Any) -> Any:
            self.assertEqual((method, plugin_id, payload["capability"]), ("cap.call", PLUGIN_ID, "llm.generate"))
            # 复现原有短预算只够思考、正文为空；预算充足时才返回完整素材。
            if payload["args"]["max_tokens"] < 8192:
                return {"success": True, "response": "", "reasoning": "正在构思", "completion_tokens": payload["args"]["max_tokens"]}
            return {"success": True, "response": json.dumps(next(responses), ensure_ascii=False), "reasoning": "已完成构思"}

        rpc = AsyncMock(side_effect=reasoning_rpc)
        runner = LLMRunner(PluginContext(PLUGIN_ID, rpc_call=rpc), "planner")
        self.engine.generator = self.engine.memory.generator = runner
        await self.engine._scene(now)
        await self.engine.memory.dreams(now)
        await self.engine.memory.diary(now)
        self.assertEqual(rpc.await_count, 3)
        self.assertEqual(self.engine.peek_state()["scene"], "安静地翻页")
        self.assertEqual((await self.engine.store.memories("dream"))[0]["payload"]["summary"], "梦见走过一条安静的小路")
        self.assertEqual((await self.engine.store.memories("diary"))[0]["payload"]["content"], "昨天读了一本书。")

    async def test_empty_scene_body_is_reported_without_saving_material(self) -> None:
        self.plugin.config.life.scene_enabled = True
        now = self.engine.environment.now()
        await self.engine.advance({"has_activity": True, "activity": {"name": "阅读", "description": "翻书",
            "goal_type": "study", "time_window": "00:00-24:00"}}, now=now)
        rpc = AsyncMock(return_value={"success": True, "response": "", "reasoning": "PRIVATE_REASONING", "completion_tokens": 8192})
        self.engine.generator = LLMRunner(PluginContext(PLUGIN_ID, rpc_call=rpc), "planner")
        with self.assertRaisesRegex(RuntimeError, "仅返回推理内容，未返回正文.*max_tokens=8192.*completion_tokens=8192") as raised:
            await self.engine._scene(now)
        self.assertNotIn("PRIVATE_REASONING", str(raised.exception))
        self.assertIsNone(await self.engine.store.get_scene(self.engine.peek_state()["activity_key"]))
        self.assertEqual(await self.engine.store.list_opportunities(), [])

    async def test_dates_commands_require_real_private_identity(self) -> None:
        denied = await self.engine.handle_command("dates add 2027-01-02 生日", stream_id="group-701", user_id="111")
        self.assertFalse(denied["success"])
        added = await self.engine.handle_command("dates add 2027-01-02 生日", stream_id="private-111", user_id="111")
        self.assertTrue(added["success"])
        date_id = added["data"]["id"]
        self.assertFalse((await self.engine.handle_command(f"dates delete {date_id}", stream_id="private-222", user_id="222"))["success"])
        self.assertTrue((await self.engine.handle_command("dates list", stream_id="private-111", user_id="111"))["data"]["dates"])
        self.assertTrue((await self.engine.handle_command(f"dates delete {date_id}", stream_id="private-111", user_id="111"))["success"])

    async def test_owner_diary_accepts_current_private_route_after_old_route(self) -> None:
        current = dict(self.rpc.private_streams["111"])
        self.rpc.private_streams["111"] = {**current, "session_id": "old-private-111", "scope": "old-adapter"}
        self.rpc.private_streams["111-current-route"] = current
        diary = {"title": "昨天的生活", "content": "在家读了一本书。"}
        await self.engine.store.save_memory(key="owner-diary", kind="diary", day="2026-10-22",
            payload=diary, now=time.time(), privacy="owner_only")
        result = await self.engine.handle_command("life diary", stream_id="private-111", user_id="111")
        self.assertTrue(result["success"])
        self.assertEqual(result["data"], diary)
        dates = await self.engine.handle_command("dates list", stream_id="private-111", user_id="111")
        self.assertTrue(dates["success"])

    async def test_owner_diary_rejects_wrong_identity_or_nonprivate_route(self) -> None:
        current = dict(self.rpc.private_streams["111"])
        variants = [{"session_id": "another-session"}, {"user_id": "222"}, {"platform": "other"},
                    {"chat_type": "group"}, {"group_id": "701"}, {"is_group_session": True}]
        for variant in variants:
            with self.subTest(variant=variant):
                self.rpc.private_streams["111"] = {**current, **variant}
                result = await self.engine.handle_command("life diary", stream_id="private-111", user_id="111")
                self.assertFalse(result["success"])
        self.rpc.private_streams["111"] = current
        self.assertFalse((await self.engine.handle_command("life diary", stream_id="private-222", user_id="222"))["success"])
        self.engine._users["111"]["enabled"] = False
        self.assertFalse((await self.engine.handle_command("life diary", stream_id="private-111", user_id="111"))["success"])

    async def test_detected_date_requires_explicit_confirmation(self) -> None:
        await self.engine.observe_message(user_id="111", session_id="private-111", message_id="date-input", text="我的生日是2027年1月2日")
        self.assertEqual(await self.engine.store.dates("111"), [])
        listed = await self.engine.handle_command("dates list", stream_id="private-111", user_id="111")
        candidate = listed["data"]["candidates"][0]
        await self.engine.handle_command(f"dates confirm {candidate['id']}", stream_id="private-111", user_id="111")
        self.assertEqual((await self.engine.store.dates("111"))[0]["event_date"], "2027-01-02")
        listed = await self.engine.handle_command("dates list", stream_id="private-111", user_id="111")
        self.assertEqual(listed["data"]["candidates"], [])

    async def test_important_date_opportunity_is_idempotent_and_target_only(self) -> None:
        now = self.engine.environment.now()
        await self.engine.store.sync_dates([ImportantDate(user_id="222", name="生日", date=now.date().isoformat())])
        await self.engine.memory.important_dates(now)
        await self.engine.memory.important_dates(now)
        items = await self.engine.store.list_opportunities(public_only=False)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["classification"], "target_only")
        self.assertIsNone(await self.engine.claim_public_opportunity())

    async def test_disabled_schedule_does_not_access_unstarted_schedule_database(self) -> None:
        class DisabledSchedule:
            async def activity_segments(self, *args: Any) -> Any:
                raise AssertionError("禁止访问未启动日程")
        self.plugin.config.schedule.enabled = False
        self.engine.schedule_engine = DisabledSchedule()
        state = await self.engine.advance({"has_activity": False, "activity": None, "error": "snapshot_not_ready"},
                                          now=self.engine.environment.now() + timedelta(minutes=1))
        self.assertEqual(state["current_activity"], "")

    async def test_scene_failure_does_not_commit_an_incomplete_scene(self) -> None:
        self.plugin.config.life.scene_enabled = True
        now = self.engine.environment.now()
        await self.engine.advance({"has_activity": True, "activity": {"name": "阅读", "description": "翻书", "goal_type": "study", "time_window": "00:00-24:00"}}, now=now)
        key = self.engine.peek_state()["activity_key"]
        for invalid_topic in ("", "\x00", None, False, {"text": "无效话题"}):
            with self.subTest(topic=invalid_topic):
                self.generator.responses.append({"scene": "安静地翻页", "topic": invalid_topic, "motive": "想分享", "shareable": True})
                with self.assertRaisesRegex(ValueError, "topic"):
                    await self.engine._scene(now)
                self.assertIsNone(await self.engine.store.get_scene(key))
                self.assertEqual(await self.engine.store.list_opportunities(), [])

    async def test_local_environment_never_reads_old_weather_cache(self) -> None:
        await self.engine.store.set_value("environment.weather", {"weather": "OLD_WEATHER", "temperature": 20})
        with patch.object(self.engine.store, "get_value", side_effect=AssertionError("不应读天气缓存")):
            await self.engine.environment.start()
            environment = self.engine.environment.snapshot()
        self.assertIn("date", environment)
        self.assertNotIn("weather", environment)
        self.assertNotIn("OLD_WEATHER", json.dumps(environment))
        self.assertNotIn("weather_enabled", self.plugin.config.environment.model_dump())

    async def test_scene_prompt_uses_real_state_and_continuous_location(self) -> None:
        self.plugin.config.life.scene_enabled = True
        now = self.engine.environment.now()
        await self.engine.advance({"has_activity": True, "activity": {"name": "阅读", "description": "翻到上次夹书签的位置",
            "goal_type": "study", "time_window": "00:00-24:00", "parameters": {"location": "书桌边"}}}, now=now)
        self.generator.responses.append({"scene": "在书桌边翻页", "shareable": False})
        await self.engine._scene(now)
        self.assertIn("书桌边", self.generator.calls[-1])
        self.assertIn("精力", self.generator.calls[-1])
        self.assertIn("不编造天气", self.generator.calls[-1])
        self.assertEqual(self.engine.peek_state()["current_location"], "书桌边")
        self.assertEqual(self.engine.peek_state()["scene"], "在书桌边翻页")


    async def test_calendar_missing_dependency_is_an_explicit_startup_error(self) -> None:
        await self.engine.stop()
        self.plugin.config.environment.calendar_enabled = True
        original_import = __import__
        def block_calendar(name: str, *args: Any, **kwargs: Any) -> Any:
            if name in {"chinese_calendar", "lunar_python"}:
                raise ImportError("测试模拟缺依赖")
            return original_import(name, *args, **kwargs)
        with patch("builtins.__import__", side_effect=block_calendar):
            with self.assertRaisesRegex(RuntimeError, "缺少 chinese-calendar 或 lunar-python"):
                await self.engine.start()
        self.assertIsNone(self.engine.store._executor)

    async def test_enabled_calendar_uses_installed_lunar_and_holiday_libraries(self) -> None:
        if find_spec("chinese_calendar") is None or find_spec("lunar_python") is None:
            self.skipTest("需要插件声明的 chinese-calendar/lunar-python 依赖")
        self.plugin.config.environment.calendar_enabled = True
        await self.engine.environment.start()
        with patch.object(self.engine.environment, "now", return_value=datetime(2026, 10, 1, 10, tzinfo=ZoneInfo("Asia/Shanghai"))):
            result = self.engine.environment.snapshot()
        self.assertTrue(result["calendar_available"])
        self.assertFalse(result["is_workday"])
        self.assertTrue(result["lunar"])
        self.assertTrue(result["holiday"])

    async def test_diary_and_date_context_respect_roles(self) -> None:
        now = self.engine.environment.now()
        await self.engine.store.save_memory(key="diary-private", kind="diary", day=now.date().isoformat(), payload={"title": "私人日记", "content": "只给主人看", "mood_summary": "平静"}, now=now.timestamp())
        await self.engine.store.add_date(user_id="111", name="考试", event_date=now.date().isoformat())
        await self.engine._refresh_views(now)
        self.assertTrue(self.engine.reply_context("111")["memory"]["diary"])
        self.assertFalse(self.engine.reply_context("222")["memory"]["diary"])
        self.assertFalse(self.engine.reply_context("222")["memory"]["important_dates"])
        self.assertNotIn("只给主人看", json.dumps(self.engine.peek_state(), ensure_ascii=False))


    async def test_stop_cancels_inflight_command_before_database_close(self) -> None:
        entered = asyncio.Event()
        async def held_command(*args: Any, **kwargs: Any) -> Any:
            entered.set()
            await asyncio.Event().wait()
        with patch.object(self.engine.commands, "execute", side_effect=held_command):
            task = asyncio.create_task(self.engine.handle_command("dates list", stream_id="private-111", user_id="111"))
            await asyncio.wait_for(entered.wait(), timeout=1)
            await self.engine.stop()
        self.assertTrue(task.cancelled())
        self.assertFalse(self.engine._tasks)
        self.assertIsNone(self.engine.store._executor)
        self.assertEqual(self.engine.peek_state()["enabled"], False)

    async def test_cancelled_sqlite_operation_finishes_before_close(self) -> None:
        started, release = threading.Event(), threading.Event()
        def held_operation() -> None:
            started.set()
            release.wait(2)
            self.engine.store.connection.execute("SELECT 1").fetchone()
        task = asyncio.create_task(self.engine.store._run(held_operation))
        await asyncio.to_thread(started.wait, 1)
        task.cancel()
        closing = asyncio.create_task(self.engine.store.close())
        await asyncio.sleep(0.01)
        self.assertFalse(closing.done())
        release.set()
        results = await asyncio.gather(task, closing, return_exceptions=True)
        self.assertIsInstance(results[0], asyncio.CancelledError)
        self.assertIsNone(results[1])
        await asyncio.gather(self.engine.store.close(), self.engine.store.close())


class ModelTests(IsolatedAsyncioTestCase):
    async def test_sdk_empty_or_invalid_body_is_not_a_successful_generation(self) -> None:
        for response in (None, "", " \n\t", {}, False):
            with self.subTest(response=response):
                rpc = AsyncMock(return_value={"success": True, "response": response})
                runner = LLMRunner(PluginContext(PLUGIN_ID, rpc_call=rpc), "planner")
                with self.assertRaisesRegex(RuntimeError, "空正文|正文类型错误"):
                    await generate_object(runner, "返回 JSON 对象")
                self.assertEqual(rpc.await_count, 1)

    def test_json_parser_preserves_complete_objects_and_rejects_invalid_output(self) -> None:
        expected = {"scene": "翻书", "shareable": False}
        for text in (json.dumps(expected), "```json\n" + json.dumps(expected) + "\n```"):
            self.assertEqual(json_object(text), expected)
        for text in ("", " \n", "```json\n \n```", "[]", "正文说明：{}", '{"scene":', '{"scene": "翻书"} trailing'):
            with self.subTest(text=text), self.assertRaisesRegex(ValueError, "空正文|JSON 对象"):
                json_object(text)


class StateTests(TestCase):
    def test_cross_midnight_sleep_is_continuous_and_emits_one_wake(self) -> None:
        tz = ZoneInfo("Asia/Shanghai")
        start = datetime(2026, 9, 10, 23, tzinfo=tz)
        end = start + timedelta(hours=8)
        state = LifeState(last_updated_at=start.timestamp(), energy=20)
        source = {"start": start, "end": end, "goal_id": "sleep", "goal_type": "sleep", "name": "睡眠", "description": "睡觉"}
        calculator = LifeStateEngine()
        applied = set()
        wakes = []
        for index in range(1, 9):
            current = {"name": "睡眠", "description": "睡觉", "goal_type": "sleep"} if index < 8 else {"name": "起床", "goal_type": "rest"}
            state, completions, batch = calculator.advance(state, start + timedelta(hours=index), [source], current, applied)
            applied.update(completions)
            wakes.extend(batch)
        self.assertEqual(len(wakes), 1)
        self.assertEqual(wakes[0]["hours"], 8)
        self.assertEqual(state.energy, 84)
        self.assertEqual(state.sleep_phase, "awake")

    def test_meal_completion_is_not_replayed(self) -> None:
        start = datetime(2026, 9, 11, 12, tzinfo=ZoneInfo("Asia/Shanghai"))
        source = {"start": start, "end": start + timedelta(hours=1), "goal_id": "lunch", "goal_type": "meal", "name": "午饭", "description": "吃饭"}
        state = LifeState(last_updated_at=start.timestamp(), hunger=60)
        calculator = LifeStateEngine()
        state, completed, _ = calculator.advance(state, start + timedelta(hours=1), [source], {}, set())
        self.assertEqual(state.hunger, 30)
        state = LifeState.from_dict(state.to_dict())
        state, replayed, _ = calculator.advance(state, start + timedelta(hours=2), [source], {}, set(completed))
        self.assertFalse(replayed)
        self.assertEqual(state.hunger, 35)

    def test_empty_schedule_never_invents_sleep_or_meals(self) -> None:
        start = datetime(2026, 9, 11, 0, tzinfo=ZoneInfo("Asia/Shanghai"))
        state = LifeState(last_updated_at=start.timestamp(), hunger=10, energy=70)
        state, completed, wakes = LifeStateEngine().advance(state, start + timedelta(hours=8), [], {}, set())
        self.assertEqual((state.energy, state.hunger, state.current_activity, state.sleep_phase), (62, 50, "", "awake"))
        self.assertFalse(completed or wakes)

    def test_activity_window_accepts_midnight_end(self) -> None:
        now = datetime(2026, 9, 11, 23, 30, tzinfo=ZoneInfo("Asia/Shanghai"))
        key, expiry = LifeEngine._activity_identity({"name": "睡觉", "time_window": "23:00-00:00"}, now)
        self.assertTrue(key)
        self.assertEqual(expiry, datetime(2026, 9, 12, 0, tzinfo=now.tzinfo).timestamp())


if __name__ == "__main__":
    main()
