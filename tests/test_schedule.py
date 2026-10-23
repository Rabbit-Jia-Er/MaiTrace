"""内置日程的隔离行为测试：使用临时 SQLite 与内存 SDK，不访问网络或发送真实消息。"""

from datetime import datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest import IsolatedAsyncioTestCase, TestCase, main
from unittest.mock import patch
from zoneinfo import ZoneInfo
import asyncio
import json
import sys
import threading

from maibot_sdk.capabilities.llm import LLMCapability


sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from MaiTrace.services.schedule import ScheduleEngine
from MaiTrace.services.schedule.config import GoalCleanupSection, ScheduleSection
from MaiTrace.services.schedule.core.models import Schedule, ScheduleItem, ScheduleType
from MaiTrace.services.schedule.database.goal_db import GoalDatabase
from MaiTrace.services.schedule.planner.auto_scheduler import ScheduleAutoScheduler
from MaiTrace.services.schedule.planner.generator import context_loader
from MaiTrace.services.schedule.planner.generator.validator import ScheduleSemanticValidator
from MaiTrace.services.schedule.planner.goal_manager import GoalManager, GoalStatus
from MaiTrace.services.schedule.utils.timezone_manager import TimezoneManager


def _schedule_item(name="午餐", time_slot="12:00", duration=1.0):
    return {
        "name": name, "description": "在家准备一顿简单的饭菜并好好休息。",
        "goal_type": "meal", "priority": "medium", "time_slot": time_slot,
        "duration_hours": duration, "parameters": {}, "conditions": {},
    }


class FakeContext:
    """记录 SDK 请求；生成结果必须由每个测试明确提供。"""

    def __init__(self) -> None:
        self.responses = []
        self.calls = []
        self.texts = []
        self.images = []
        self.renders = []
        self.frequencies = []
        self.proactive = []
        self.config = SimpleNamespace(get=self.get_config, get_plugin=self.get_plugin_config)
        self.llm = LLMCapability(self)
        self.send = SimpleNamespace(text=self.send_text, image=self.send_image)
        self.render = SimpleNamespace(html2png=self.render_html)
        self.chat = SimpleNamespace(
            get_stream_by_group_id=self.group_stream,
            get_stream_by_user_id=self.private_stream,
        )
        self.frequency = SimpleNamespace(set_adjust=self.adjust_frequency)
        self.maisaka = SimpleNamespace(trigger_proactive=self.trigger)

    async def get_config(self, name, default=None):
        return {
            "personality.personality": "爱做饭，也喜欢读书的麦麦",
            "personality.reply_style": "自然简洁",
            "personality.multiple_reply_style": [],
            "personality.multiple_probability": 0.0,
            "bot.nickname": "麦麦", "bot.alias_names": [],
        }.get(name, default)

    async def get_plugin_config(self, plugin_id):
        return {}

    async def call_capability(self, capability, timeout_ms=None, **kwargs):
        self.calls.append((capability, timeout_ms, kwargs))
        if capability != "llm.generate":
            raise AssertionError(f"未预期的 SDK 能力: {capability}")
        if not self.responses:
            raise AssertionError("测试没有提供 LLM 返回")
        result = self.responses.pop(0)
        if isinstance(result, Exception):
            raise result
        return {"success": True, "response": result}

    async def send_text(self, text, stream_id):
        self.texts.append((text, stream_id))
        return True

    async def send_image(self, image_data, stream_id):
        self.images.append((image_data, stream_id))
        return True

    async def render_html(self, html, **kwargs):
        self.renders.append((html, kwargs))
        return {"image_base64": "aW1hZ2U=", "mime_type": "image/png"}

    async def group_stream(self, target_id, platform="qq"):
        return {"session_id": f"real-group-{target_id}", "platform": platform}

    async def private_stream(self, target_id, platform="qq"):
        if target_id == "789":
            return None
        return {"session_id": f"real-private-{target_id}", "platform": platform}

    async def adjust_frequency(self, stream_id, factor):
        self.frequencies.append((stream_id, factor))
        return True

    async def trigger(self, **kwargs):
        self.proactive.append(kwargs)
        return {"success": True, "task_id": f"schedule-task-{len(self.proactive)}"}


class ScheduleEngineTests(IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = TemporaryDirectory(prefix="maitrace-schedule-test-")
        self.now = datetime(2026, 9, 11, 12, 1, tzinfo=ZoneInfo("Asia/Shanghai"))
        self.clock_patch = patch.object(TimezoneManager, "get_now", lambda _manager: self.now)
        self.clock_patch.start()
        self.ctx = FakeContext()
        self.config = SimpleNamespace(
            schedule=ScheduleSection(
                auto_schedule_enabled=False, auto_generate=False, use_multi_round=False,
                min_activities=1, max_activities=4, min_description_length=5,
                max_description_length=40, llm_log_enabled=False,
                history_message_limit=0, knowledge_search_limit=0,
            ),
            goal_cleanup=GoalCleanupSection(),
            persona=SimpleNamespace(use_multiple_reply_style=False, self_description=""),
        )
        self.plugin = SimpleNamespace(ctx=self.ctx, config=self.config)
        self.engine = ScheduleEngine(self.plugin, data_dir=Path(self.temp.name) / "schedule")
        self.db_threads = set()
        original_connection = GoalDatabase._get_connection

        def traced_connection(database):
            self.db_threads.add(threading.get_ident())
            return original_connection(database)

        self.connection_patch = patch.object(GoalDatabase, "_get_connection", traced_connection)
        self.connection_patch.start()
        await self.engine.start()
        # 等首次清理完成再准备测试数据，后台不会在测试中访问任何 SDK 网络。
        await asyncio.sleep(0)

    async def asyncTearDown(self):
        await self.engine.stop()
        self.connection_patch.stop()
        self.clock_patch.stop()
        self.temp.cleanup()

    async def add_goal(self, name, window, *, when=None, status="active", parameters=None):
        values = dict(parameters or {})
        values["time_window"] = window
        goal_id = f"test-{name}-{when or self.now}"
        await self.engine.run_db(
            self.engine.goal_manager.db.create_goal,
            goal_id=goal_id, name=name, description=name,
            goal_type="meal" if name != "睡觉" else "sleep", priority="medium",
            creator_id="test", chat_id="global", parameters=values,
            created_at=when or self.now, status=status,
        )
        return goal_id


    async def test_database_ownership_and_thread_shutdown(self):
        main_thread = threading.get_ident()
        goal_id = await self.add_goal("阅读", [720, 780])
        manager = self.engine.goal_manager
        self.assertTrue(await self.engine.run_db(manager.update_goal, goal_id, description="读完这一章"))
        self.assertEqual((await self.engine.run_db(manager.get_goal, goal_id)).description, "读完这一章")
        other = ScheduleEngine(self.plugin, data_dir=Path(self.temp.name) / "other")
        await other.start()
        try:
            self.assertEqual(await other.run_db(other.goal_manager.db.count_goals), 0)
            self.assertIsNone(await other.run_db(other.goal_manager.get_goal, goal_id))
        finally:
            await other.stop()
        for retired in ("manage_goal", "apply_schedule"):
            self.assertEqual((await self.engine.handle_tool(retired))["type"], "error")
        self.assertIsNotNone(await self.engine.run_db(manager.get_goal, goal_id))
        self.assertNotIn(main_thread, self.db_threads)
        database = manager.db
        await self.engine.stop()
        with self.assertRaisesRegex(RuntimeError, "已关闭"):
            database._get_connection()

    async def test_cross_midnight_query_and_cleanup_preserve_sleep(self):
        self.now = self.now.replace(hour=1, minute=0)
        yesterday = self.now - timedelta(days=1)
        sleep_id = await self.add_goal("睡觉", [1380, 1860], when=yesterday)
        await self.add_goal("今晚新日程", [1380, 1860])
        await self.engine.refresh(force=True)
        current = self.engine.peek().current
        self.assertIsNotNone(current)
        self.assertEqual(current.goal_id, sleep_id)
        self.assertEqual(current.start_at.date(), yesterday.date())
        await self.engine.cleanup()
        goal = await self.engine.run_db(self.engine.goal_manager.get_goal, sleep_id)
        self.assertEqual(goal.status, GoalStatus.ACTIVE)
        self.now = self.now.replace(hour=7)
        await self.engine.cleanup()
        goal = await self.engine.run_db(self.engine.goal_manager.get_goal, sleep_id)
        self.assertEqual(goal.status, GoalStatus.COMPLETED)
        await self.engine.refresh(force=True)
        self.assertIsNone(self.engine.peek().current)

    async def test_snapshot_peek_has_no_database_io_and_refresh_failure_is_visible(self):
        await self.add_goal("午餐", [720, 780])
        await self.engine.refresh(force=True)
        snapshot = self.engine.peek()
        with patch.object(self.engine.goal_manager, "get_active_goals", side_effect=RuntimeError("数据库损坏")):
            self.assertIs(self.engine.peek(), snapshot)
            with self.assertRaisesRegex(RuntimeError, "数据库损坏"):
                await self.engine.refresh(force=True)
        self.assertIsNone(self.engine.peek())

    async def test_generation_uses_explicit_timeout_and_skips_existing_day(self):
        self.ctx.responses.append(json.dumps({"schedule_items": [_schedule_item()]}, ensure_ascii=False))
        answer = await self.engine.handle_tool("generate_schedule")
        self.assertEqual(answer["type"], "schedule_generated", answer)
        self.assertEqual(await self.engine.run_db(self.engine.goal_manager.db.count_goals), 1)
        self.assertEqual(self.ctx.calls[0][1], int(self.config.schedule.generation_timeout * 1000))
        second = await self.engine.handle_tool("generate_schedule")
        self.assertEqual(second["type"], "schedule_generated")
        self.assertEqual(len(self.ctx.calls), 1)

    async def test_failed_regeneration_keeps_previous_schedule(self):
        goal_id = await self.add_goal("原有午餐", [720, 780])
        self.ctx.responses.append(RuntimeError("模型请求失败"))
        with self.assertLogs(level="WARNING"):
            result = await self.engine.handle_command("regenerate")
        self.assertFalse(result[0])
        self.assertIsNotNone(await self.engine.run_db(self.engine.goal_manager.get_goal, goal_id))
        self.assertEqual(await self.engine.run_db(self.engine.goal_manager.db.count_goals), 1)

    async def test_overlap_retries_even_when_quality_threshold_would_pass(self):
        self.config.schedule.use_multi_round = True
        self.config.schedule.max_rounds = 2
        self.config.schedule.quality_threshold = 0.1
        self.ctx.responses.extend([
            json.dumps({"schedule_items": [
                _schedule_item("推进项目", "09:00", 2), _schedule_item("整理资料", "10:00", 1),
            ]}, ensure_ascii=False),
            json.dumps({"schedule_items": [_schedule_item()]}, ensure_ascii=False),
        ])
        result = await self.engine.handle_tool("generate_schedule")
        self.assertEqual(result["type"], "schedule_generated", result)
        self.assertEqual(len(self.ctx.calls), 2)
        self.assertIn("时间重叠", self.ctx.calls[-1][2]["prompt"])
        self.assertEqual(await self.engine.run_db(self.engine.goal_manager.db.count_goals), 1)

    async def test_overlapping_regeneration_and_manual_apply_keep_previous_schedule(self):
        old_id = await self.add_goal("原有午餐", [720, 780])
        overlap = [_schedule_item("新午餐", "12:00", 2), _schedule_item("午后整理", "13:00", 1)]
        self.ctx.responses.append(json.dumps({"schedule_items": overlap}, ensure_ascii=False))
        with self.assertLogs(level="ERROR"):
            result = await self.engine.handle_command("regenerate")
        self.assertFalse(result[0])
        self.assertIn("重叠", result[1])
        commitment = await self.engine.run_db(
            self.engine.goal_manager.add_pending_commitment, self.now.strftime("%Y-%m-%d"), "午餐约定", time="12:00",
        )
        data = Schedule(ScheduleType.DAILY, "冲突日程", [ScheduleItem.from_dict(item) for item in overlap])
        generator = self.engine.build_generator()
        with self.assertRaisesRegex(ValueError, "重叠"):
            await generator.apply_schedule(data, user_id="system", chat_id="global")
        self.assertIsNotNone(await self.engine.run_db(self.engine.goal_manager.get_goal, old_id))
        self.assertEqual((await self.engine.run_db(self.engine.goal_manager.get_goal, commitment.goal_id)).status, GoalStatus.ACTIVE)

    async def test_history_preserves_order_and_continuity_details_in_both_summaries(self):
        goals = [SimpleNamespace(
            name="整理项目", description=f"PLACE_{hour}：承接未完成的项目进度。",
            parameters={"time_window": [hour * 60, hour * 60 + 30]}, conditions={},
            goal_type="custom", priority=SimpleNamespace(value="medium"),
            status=SimpleNamespace(value="active"), progress=0,
        ) for hour in reversed(range(6, 21))]
        scheduler = ScheduleAutoScheduler(self.plugin, engine=self.engine)
        loader = context_loader.ScheduleContextLoader(self.engine.goal_manager, scheduler.tz_manager)
        with patch.object(self.engine.goal_manager, "get_schedule_goals", return_value=goals):
            summaries = [
                loader.load_recent_schedule_summary(days=1),
                scheduler._build_recent_schedule_summary(self.engine.goal_manager, 1, True),
            ]
        for summary in summaries:
            self.assertIn("PLACE_6", summary)
            self.assertIn("PLACE_20", summary)
            self.assertLess(summary.index("06:00"), summary.index("20:00"))

    async def test_prepare_generation_deadline_covers_persona_and_interest(self):
        await self.engine.prepare_generation()
        previous_profile = self.engine.bot_profile
        original_get = self.ctx.config.get
        for slow_key in ("personality.personality", "personality.interest"):
            with self.subTest(slow_key=slow_key):
                blocked, cancelled = asyncio.Event(), asyncio.Event()

                async def slow_get(key, default=None):
                    if key == slow_key:
                        try:
                            await blocked.wait()
                        finally:
                            cancelled.set()
                    return await original_get(key, default)

                with patch("MaiTrace.services.schedule.PREPARE_GENERATION_TIMEOUT_SECONDS", 0.05), \
                        patch.object(self.ctx.config, "get", side_effect=slow_get):
                    with self.assertRaisesRegex(TimeoutError, "人格配置读取超过整体截止时间"):
                        await asyncio.wait_for(self.engine.prepare_generation(), timeout=1)
                    self.assertTrue(cancelled.is_set())
                    self.assertEqual(self.engine.bot_profile, previous_profile)
                    with self.assertLogs(level="ERROR"):
                        result = await self.engine.handle_tool("generate_schedule")
                self.assertEqual(result["type"], "error")
                self.assertIn("人格配置读取超过整体截止时间", result["content"])
                self.assertEqual(self.ctx.calls, [])

    async def test_history_queries_bound_concurrency_and_preserve_order_and_scope(self):
        loader = context_loader.ScheduleContextLoader(self.engine.goal_manager, self.engine.timezone_manager)
        allowed = [f"qq:group:{index}" for index in range(9)]
        streams = [
            {"session_id": f"stream-{index}", "platform": "qq", "group_id": str(index)}
            for index in range(9)
        ]
        streams.extend([
            {"session_id": "excluded-group", "platform": "qq", "group_id": "outside", "user_id": "0"},
            {"session_id": "excluded-private", "platform": "qq", "user_id": "0"},
        ])
        release, first_batch = asyncio.Event(), asyncio.Event()
        queries = []
        active = 0
        peak = 0

        async def list_streams(*, platform):
            self.assertEqual(platform, "qq")
            return streams

        async def read_history(stream_id, start, end, *, limit):
            nonlocal active, peak
            queries.append((stream_id, start, end, limit))
            active += 1
            peak = max(peak, active)
            if len(queries) == 4:
                first_batch.set()
            try:
                await release.wait()
                index = int(stream_id.split("-")[1])
                # 较早的流较慢，结果仍须按原流顺序合并，不能取最快返回的五条。
                await asyncio.sleep((9 - index) * 0.001)
                return [{"processed_plain_text": f"聊天记录-{index}", "timestamp": self.now.timestamp()}]
            finally:
                active -= 1

        self.ctx.chat.get_all_streams = list_streams
        self.ctx.message = SimpleNamespace(get_by_time_in_chat=read_history)
        loading = asyncio.create_task(loader.load_recent_history_across_streams(self.plugin, allowed, limit=5))
        try:
            await asyncio.wait_for(first_batch.wait(), timeout=1)
            self.assertEqual(len(queries), 4)
            release.set()
            result = await asyncio.wait_for(loading, timeout=2)
        finally:
            release.set()
            await asyncio.gather(loading, return_exceptions=True)
        self.assertEqual(peak, 4)
        self.assertEqual(active, 0)
        self.assertEqual({query[0] for query in queries}, {f"stream-{index}" for index in range(9)})
        self.assertEqual({query[3] for query in queries}, {1})
        self.assertEqual(len({query[1:3] for query in queries}), 1)
        start, end = map(float, queries[0][1:3])
        self.assertEqual(datetime.fromtimestamp(start).time(), datetime.min.time())
        self.assertGreaterEqual(end, start)
        self.assertEqual(
            [line.rsplit(": ", 1)[1] for line in result.splitlines()],
            [f"聊天记录-{index}" for index in range(4, 9)],
        )

    async def test_history_deadline_covers_discovery_and_cancels_all_readers(self):
        loader = context_loader.ScheduleContextLoader(self.engine.goal_manager, self.engine.timezone_manager)
        for phase in ("discovery", "history"):
            with self.subTest(phase=phase):
                blocked = asyncio.Event()
                requested, cancelled, readers = [], [], []

                async def list_streams(*, platform):
                    if phase == "discovery":
                        try:
                            await blocked.wait()
                        finally:
                            cancelled.append("discovery")
                    return [{"session_id": f"stream-{index}"} for index in range(10)]

                async def read_history(stream_id, start, end, *, limit):
                    requested.append(stream_id)
                    readers.append(asyncio.current_task())
                    try:
                        await blocked.wait()
                    finally:
                        cancelled.append(stream_id)

                self.ctx.chat.get_all_streams = list_streams
                self.ctx.message = SimpleNamespace(get_by_time_in_chat=read_history)
                with patch.object(context_loader, "HISTORY_QUERY_TIMEOUT_SECONDS", 0.05):
                    with self.assertRaisesRegex(TimeoutError, "整体截止时间"):
                        await asyncio.wait_for(
                            loader.load_recent_history_across_streams(self.plugin, ["all"], limit=30),
                            timeout=1,
                        )
                if phase == "discovery":
                    self.assertEqual(requested, [])
                    self.assertEqual(cancelled, ["discovery"])
                else:
                    self.assertEqual(len(requested), 4)
                    self.assertCountEqual(cancelled, requested)
                    self.assertTrue(all(reader.done() for reader in readers))

    async def test_history_failures_abort_regeneration_without_replacing_schedule(self):
        goal_id = await self.add_goal("原有午餐", [720, 780])
        self.config.schedule.history_message_limit = 30
        for failure, expected in (("sdk", "历史服务不可用"), ("deadline", "整体截止时间")):
            with self.subTest(failure=failure):
                blocked = asyncio.Event()

                async def list_streams(*, platform):
                    return [{"session_id": "existing-stream"}]

                async def read_history(stream_id, start, end, *, limit):
                    if failure == "sdk":
                        raise RuntimeError("历史服务不可用")
                    await blocked.wait()

                self.ctx.chat.get_all_streams = list_streams
                self.ctx.message = SimpleNamespace(get_by_time_in_chat=read_history)
                with patch.object(context_loader, "HISTORY_QUERY_TIMEOUT_SECONDS", 0.05):
                    with self.assertLogs(level="ERROR"):
                        result = await self.engine.handle_command("regenerate")
                self.assertFalse(result[0])
                self.assertIn(expected, result[1])
                self.assertEqual(self.ctx.calls, [])
                self.assertIsNotNone(await self.engine.run_db(self.engine.goal_manager.get_goal, goal_id))
                self.assertEqual(await self.engine.run_db(self.engine.goal_manager.db.count_goals), 1)

    async def test_batch_write_rolls_back_and_consumes_only_matched_commitments(self):
        manager = self.engine.goal_manager
        first = {"name": "第一条", "description": "描述", "goal_type": "custom", "creator_id": "test", "chat_id": "global"}
        invalid = {**first, "name": "无效优先级", "priority": "invalid"}
        with self.assertLogs(level="ERROR"):
            with self.assertRaises(ValueError):
                await self.engine.run_db(manager.create_goals_batch, [first, invalid])
        self.assertEqual(await self.engine.run_db(manager.db.count_goals), 0)
        date = self.now.strftime("%Y-%m-%d")
        matched = await self.engine.run_db(
            manager.add_pending_commitment, date, "一起吃饭", time="12:00", target_stream_id="real-private-123",
        )
        unmatched = await self.engine.run_db(manager.add_pending_commitment, date, "晚上散步", time="20:00")
        data = Schedule(ScheduleType.DAILY, "今天", [ScheduleItem.from_dict(_schedule_item("一起吃饭", duration=0.5))])
        ids = await self.engine.build_generator().apply_schedule(data, user_id="system", chat_id="global")
        self.assertEqual(len(ids), 1)
        self.assertEqual((await self.engine.run_db(manager.get_goal, matched.goal_id)).status, GoalStatus.COMPLETED)
        applied = await self.engine.run_db(manager.get_goal, ids[0])
        self.assertEqual(applied.parameters["commitment_goal_id"], matched.goal_id)
        self.assertEqual(applied.parameters["commitment_stream_id"], "real-private-123")
        remaining = await self.engine.run_db(manager.get_pending_commitments, date)
        self.assertEqual([goal.goal_id for goal in remaining], [unmatched.goal_id])

    async def test_role_judge_future_and_reject_do_not_replace_today(self):
        original = await self.add_goal("原有午餐", [720, 780])
        tomorrow = (self.now + timedelta(days=1)).strftime("%Y-%m-%d")
        self.ctx.responses.append(json.dumps({"decision": "future", "date": tomorrow, "time": "18:00", "title": "散步", "reason": "明天有空"}))
        future = await self.engine.handle_tool(
            "update_schedule", description="明天六点散步", stream_id="real-private-123", user_id="123",
        )
        self.assertEqual(future["type"], "schedule_committed", future)
        self.ctx.responses.append(json.dumps({"decision": "reject", "reason": "今天没有空"}))
        rejected = await self.engine.handle_tool("update_schedule", description="现在出去玩")
        self.assertEqual(rejected["type"], "schedule_unchanged")
        self.assertIsNotNone(await self.engine.run_db(self.engine.goal_manager.get_goal, original))
        pending = await self.engine.run_db(self.engine.goal_manager.get_pending_commitments, tomorrow)
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0].creator_id, "123")
        self.assertEqual(pending[0].parameters["target_stream_id"], "real-private-123")
        self.now += timedelta(days=1)
        self.ctx.responses.append(json.dumps({"schedule_items": [_schedule_item("散步", "18:00", 0.5)]}, ensure_ascii=False))
        generated = await self.engine.handle_tool("generate_schedule")
        self.assertEqual(generated["type"], "schedule_generated", generated)
        goals = await self.engine.run_db(self.engine.goal_manager.get_schedule_goals, chat_id="global")
        today = [goal for goal in goals if goal.created_at.date() == self.now.date()]
        self.assertEqual(len(today), 1)
        self.assertEqual(today[0].parameters["commitment_stream_id"], "real-private-123")
        self.assertEqual(today[0].parameters["commitment_goal_id"], pending[0].goal_id)

    async def test_generator_receives_life_context_and_cross_day_policy(self):
        self.config.schedule.cross_day_activity = False
        self.engine.update_life_state({"enabled": True, "energy": 37, "scene": "在阳台看雨"})
        await self.engine.prepare_generation()
        generator = self.engine.build_generator()
        prompt = generator.base_generator.prompt_builder.build_schedule_prompt("daily", {})
        self.assertIn("在阳台看雨", prompt)
        self.assertIn('"energy": 37', prompt)
        self.assertFalse(generator.base_generator.config["cross_day_activity"])

    async def test_inference_runs_when_daily_generation_is_disabled(self):
        self.config.schedule.auto_infer_next_day_prompt = True
        self.config.schedule.infer_time = "00:00"
        scheduler = ScheduleAutoScheduler(self.plugin, engine=self.engine)
        from unittest.mock import AsyncMock
        with patch.object(scheduler, "_infer_next_day_prompt", AsyncMock(return_value=True)) as infer, \
             patch.object(scheduler, "_generate_today_schedule", AsyncMock()) as generate:
            await scheduler.start()
            try:
                for _ in range(100):
                    if infer.await_count:
                        break
                    await asyncio.sleep(0.001)
                self.assertTrue(scheduler.is_running)
                infer.assert_awaited_once()
                generate.assert_not_awaited()
            finally:
                await scheduler.stop()

    async def test_nightly_inference_is_saved_and_used_on_next_day(self):
        await self.add_goal("午餐", [720, 780])
        self.config.schedule.custom_prompt = "FIXED_MULTI_DAY_PROJECT：保持当前地点和进行中的长期项目。"
        scheduler = ScheduleAutoScheduler(self.plugin, engine=self.engine)
        strategy = "明天在完成必要学习以后安排轻松的阅读，延续今天的兴趣并给休息留出足够时间。"
        self.ctx.responses.append(strategy)
        await self.engine.prepare_generation()
        self.assertTrue(await scheduler._infer_next_day_prompt(self.engine.build_generator_config()))
        target_date = (self.now + timedelta(days=1)).strftime("%Y-%m-%d")
        self.assertIn(target_date, self.ctx.calls[-1][2]["prompt"])
        self.assertIn("今天", self.ctx.calls[-1][2]["prompt"])
        self.assertEqual(scheduler._get_effective_custom_prompt(self.now.strftime("%Y-%m-%d"), "fixed"), "fixed")
        cache = self.engine.data_dir / "next_day_prompt.json"
        self.assertTrue(cache.is_file())
        self.now += timedelta(days=1)
        self.ctx.responses.append(json.dumps({"schedule_items": [_schedule_item("新一天午餐")]}, ensure_ascii=False))
        await scheduler._generate_today_schedule()
        self.assertIn(strategy, self.ctx.calls[-1][2]["prompt"])
        self.assertIn("FIXED_MULTI_DAY_PROJECT", self.ctx.calls[-1][2]["prompt"])
        self.assertEqual(len(await self.engine.run_db(self.engine.goal_manager.get_schedule_goals)), 1)

    async def test_proactive_resolves_existing_streams_and_deduplicates_commitment(self):
        self.config.schedule.proactive_streams = ["qq:private:123", "qq:private:456", "qq:private:789"]
        self.config.schedule.enable_proactive_trigger = True
        self.config.schedule.enable_frequency_modulation = True
        await self.add_goal("午餐", [720, 780], parameters={
            "is_commitment": True, "commitment_title": "午餐提醒", "commitment_time": "12:00",
            "commitment_stream_id": "real-private-123"
        })
        await self.engine.patrol_proactive()
        await self.engine.patrol_proactive()
        self.assertEqual(len(self.ctx.proactive), 1)
        self.assertEqual(self.ctx.proactive[0]["stream_id"], "real-private-123")
        self.assertEqual(self.ctx.proactive[0]["priority"], "high")
        self.assertIn("午餐提醒", self.ctx.proactive[0]["intent"])
        self.assertEqual(self.ctx.frequencies, [("real-private-123", 1.2), ("real-private-456", 1.2)])
        # 无目标会话的旧约定可保留为生活安排，但不能向白名单所有人广播。
        self.now += timedelta(days=1)
        await self.add_goal("旧约定", [720, 780], parameters={
            "is_commitment": True, "commitment_title": "旧提醒", "commitment_time": "12:00",
        })
        await self.engine.patrol_proactive()
        self.assertEqual(len(self.ctx.proactive), 1)
        await self.engine.stop()
        self.assertIn(("real-private-123", 1.0), self.ctx.frequencies)

    async def test_frequency_requires_true_retries_failures_and_restores_on_stop(self):
        self.config.schedule.proactive_streams = ["qq:private:123"]
        self.config.schedule.enable_frequency_modulation = True
        self.config.schedule.enable_proactive_trigger = False
        await self.add_goal("午餐", [720, 780])
        service = self.engine._proactive
        responses = [False, {"success": True}, True, False, True]
        calls = []

        async def adjust(stream_id, factor):
            calls.append((stream_id, factor))
            return responses.pop(0)

        with patch.object(self.ctx.frequency, "set_adjust", side_effect=adjust):
            for _failure in range(2):
                with self.assertLogs(level="WARNING"):
                    await self.engine.patrol_proactive()
                self.assertNotIn("real-private-123", service._current_factor)
            await self.engine.patrol_proactive()
            self.assertEqual(service._current_factor["real-private-123"], 1.2)
            await self.engine.patrol_proactive()
            self.assertEqual(len(calls), 3)
            with self.assertLogs(level="WARNING"):
                await service.stop()
            self.assertEqual(service._current_factor["real-private-123"], 1.2)
            # 恢复失败仍保留实际施加的因子；再次停止会重试，成功后才记录为默认值。
            await service.stop()
            self.assertEqual(service._current_factor["real-private-123"], 1.0)
        self.assertEqual(calls, [("real-private-123", 1.2)] * 3 + [("real-private-123", 1.0)] * 2)
        self.assertEqual(responses, [])

    async def test_activity_segments_keep_real_boundaries_and_leave_gaps(self):
        await self.add_goal("午餐", [720, 780])
        segments = await self.engine.activity_segments(self.now, self.now + timedelta(minutes=10))
        self.assertEqual(len(segments), 1)
        self.assertEqual(segments[0]["start"].minute, 0)
        self.assertEqual(segments[0]["end"].hour, 13)
        self.assertEqual(await self.engine.activity_segments(self.now + timedelta(hours=2), self.now + timedelta(hours=3)), [])

    async def test_plan_list_escapes_html_and_delete_uses_displayed_order(self):
        second = await self.add_goal("晚餐", [1080, 1140])
        first = await self.add_goal("<script>午餐</script>", [720, 780])
        result = await self.engine.handle_command("/zn plan list", stream_id="test-stream")
        self.assertTrue(result[0], result)
        self.assertIn("&lt;script&gt;午餐&lt;/script&gt;", self.ctx.renders[0][0])
        self.assertFalse(self.ctx.renders[0][1]["allow_network"])
        deleted = await self.engine.handle_command("delete 1")
        self.assertTrue(deleted[0])
        self.assertIsNone(await self.engine.run_db(self.engine.goal_manager.get_goal, first))
        self.assertIsNotNone(await self.engine.run_db(self.engine.goal_manager.get_goal, second))


    async def test_status_generates_today_once_and_preserves_existing_items(self):
        self.config.schedule.auto_generate = True
        self.ctx.responses.append(json.dumps({"schedule_items": [_schedule_item()]}, ensure_ascii=False))
        first = await self.engine.handle_tool("get_planning_status", detailed=True)
        self.assertEqual(first["type"], "planning_status", first)
        self.assertIn("正在进行", first["content"])
        self.assertEqual(len(first["data"]), 1)
        second = await self.engine.handle_tool("get_planning_status", detailed=True)
        self.assertEqual(second["data"], first["data"])
        self.assertEqual(len(self.ctx.calls), 1)


    async def test_retired_schedule_types_reject_before_model_and_database_writes(self):
        for schedule_type in ("weekly", "monthly"):
            result = await self.engine.handle_tool("generate_schedule", schedule_type=schedule_type)
            self.assertEqual(result["type"], "error", result)
            self.assertIn("每日生活作息", result["content"])
            schedule = Schedule(
                ScheduleType(schedule_type), "旧版计划",
                [ScheduleItem.from_dict(_schedule_item())],
            )
            with self.assertRaisesRegex(ValueError, "每日生活作息"):
                await self.engine.build_generator().apply_schedule(schedule, user_id="system", chat_id="global")
        self.assertFalse(self.ctx.calls)
        self.assertEqual(await self.engine.run_db(self.engine.goal_manager.db.count_goals), 0)

    async def test_daily_multiround_quality_keeps_best_generation(self):
        self.config.schedule.use_multi_round = True
        self.config.schedule.max_rounds = 2
        self.config.schedule.quality_threshold = 0.82
        poor = {**_schedule_item("午夜午餐", "03:00"), "description": "吃一口饭"}
        better = {
            **_schedule_item("好好吃午餐"),
            "description": "准备喜欢的蔬菜和米饭，安静享用一顿午餐，随后收拾餐具并休息片刻。",
        }
        self.ctx.responses.extend([
            json.dumps({"schedule_items": [poor]}, ensure_ascii=False),
            json.dumps({"schedule_items": [better]}, ensure_ascii=False),
        ])
        generated = await self.engine.handle_tool("generate_schedule", auto_apply=False)
        self.assertEqual(generated["type"], "schedule_generated", generated)
        self.assertEqual(generated["data"]["schedule_type"], "daily")
        self.assertEqual(generated["data"]["items"][0]["name"], "好好吃午餐")
        self.assertIn("午餐时间不合理", self.ctx.calls[-1][2]["prompt"])
        self.assertEqual(len(self.ctx.calls), 2)
        self.assertEqual(await self.engine.run_db(self.engine.goal_manager.db.count_goals), 0)

    async def test_ordinary_activity_switch_does_not_send_announcement(self):
        self.config.schedule.proactive_streams = ["qq:private:123"]
        self.config.schedule.enable_proactive_trigger = True
        await self.add_goal("午餐", [720, 780])
        self.ctx.responses.append(json.dumps({"should_speak": False}))
        await self.engine.patrol_proactive()
        await self.engine._proactive._activity_task
        self.assertEqual(self.ctx.proactive, [])
        self.assertEqual(self.ctx.frequencies, [("real-private-123", 1.2)])

    async def test_role_judge_today_inserts_without_replacing_and_rejects_invalid_dates(self):
        previous = await self.add_goal("原午餐", [720, 780])
        before = (await self.engine.run_db(self.engine.goal_manager.get_goal, previous)).to_dict()
        self.ctx.responses.append(json.dumps({"decision": "today", "title": "午餐后阅读", "time": "13:00", "reason": "符合兴趣"}))
        result = await self.engine.handle_tool("update_schedule", description="午饭后想读书")
        self.assertEqual(result["type"], "schedule_inserted", result)
        self.assertEqual((await self.engine.run_db(self.engine.goal_manager.get_goal, previous)).to_dict(), before)
        self.assertEqual(result["data"]["parameters"]["time_window"], [780, 810])
        self.assertEqual(len(self.ctx.calls), 1)
        self.ctx.responses.append(json.dumps({"decision": "future", "title": "不存在日期", "date": "2026-99-99"}))
        with self.assertLogs(level="ERROR"):
            invalid = await self.engine.handle_tool("update_schedule", description="未来约定")
        self.assertEqual(invalid["type"], "error")
        self.assertEqual(await self.engine.run_db(self.engine.goal_manager.db.count_goals), 2)

    async def test_disabled_schedule_stops_proactive_and_reload_preserves_database(self):
        self.config.schedule.proactive_streams = ["qq:private:123"]
        self.config.schedule.enable_frequency_modulation = True
        goal_id = await self.add_goal("午餐", [720, 780], parameters={"is_commitment": True, "commitment_title": "午餐提醒", "commitment_stream_id": "real-private-123"})
        await self.engine.patrol_proactive()
        self.assertEqual(len(self.ctx.proactive), 1)
        self.config.schedule.enabled = False
        await self.engine.patrol_proactive()
        self.assertEqual(len(self.ctx.proactive), 1)
        self.assertEqual(self.ctx.frequencies[-1], ("real-private-123", 1.0))
        self.assertIsNone(self.engine.peek())
        answer = await self.engine.handle_tool("generate_schedule")
        self.assertEqual(answer["type"], "error")
        old_database = self.engine.goal_manager.db
        await self.engine.reload_config()
        self.assertFalse(self.engine.started)
        self.assertTrue(old_database._closed)
        self.config.schedule.enabled = True
        await self.engine.reload_config()
        self.assertTrue(self.engine.started)
        self.assertIsNotNone(await self.engine.run_db(self.engine.goal_manager.get_goal, goal_id))

    async def test_commitments_accept_distant_dates_and_reject_past_dates_before_storage(self):
        manager = self.engine.goal_manager
        accepted_days = (0, 1, 3, 4, 7, 365, 3650, 10000)
        for days in accepted_days:
            target = (self.now + timedelta(days=days)).date().isoformat()
            accepted = await self.engine.run_db(manager.add_pending_commitment, target, f"约定{days}", duration_minutes=45)
            self.assertEqual(accepted.parameters["duration_minutes"], 45)
        for days in (-1, -365):
            with self.subTest(days=days), self.assertRaisesRegex(ValueError, "过去"):
                await self.engine.run_db(manager.add_pending_commitment,
                    (self.now + timedelta(days=days)).date().isoformat(), "不应写入")
        for duration in (True, 14, 721, 30.5):
            with self.subTest(duration=duration), self.assertRaisesRegex(ValueError, "时长"):
                await self.engine.run_db(manager.add_pending_commitment, self.now.date().isoformat(), "不应写入", duration_minutes=duration)
        legacy = await self.engine.run_db(manager.db.create_goal, goal_id="legacy-far-future", name="旧约定",
            description="以前记录", goal_type="pending_commitment", priority="medium", creator_id="old", chat_id="global",
            parameters={"commitment_date": (self.now + timedelta(days=20)).date().isoformat()})
        self.assertTrue(legacy)
        self.assertEqual(await self.engine.run_db(manager.db.count_goals), len(accepted_days) + 1)
        await self.engine.refresh(force=True)
        self.assertEqual(
            {item["date"] for item in self.engine.peek().commitments},
            {(self.now + timedelta(days=days)).date().isoformat() for days in (*accepted_days, 20)},
        )

    async def test_today_conflict_preserves_all_fields_and_reports_free_intervals(self):
        previous = await self.add_goal("原午餐", [720, 780])
        second = await self.add_goal("下午安排", [840, 900])
        before = [(await self.engine.run_db(self.engine.goal_manager.get_goal, key)).to_dict() for key in (previous, second)]
        self.ctx.responses.append(json.dumps({"decision": "today", "title": "一起聊天", "time": "12:30", "duration_minutes": 30}))
        with self.assertLogs(level="ERROR"):
            result = await self.engine.handle_tool("update_schedule", description="今天12:30聊半小时", stream_id="real-private-123")
        self.assertEqual(result["type"], "schedule_unchanged", result)
        self.assertIn("13:00–14:00", result["available_times"])
        after = [(await self.engine.run_db(self.engine.goal_manager.get_goal, key)).to_dict() for key in (previous, second)]
        self.assertEqual(before, after)
        self.assertEqual(await self.engine.run_db(self.engine.goal_manager.db.count_goals), 2)
        inserted = await self.engine.run_db(self.engine.goal_manager.insert_today_commitment,
            "空档约定", target_stream_id="real-private-123", duration_minutes=30)
        self.assertEqual(inserted.parameters["time_window"], [780, 810])
        inserted_second = await self.engine.run_db(self.engine.goal_manager.insert_today_commitment,
            "另一个约定", target_stream_id="real-private-123", duration_minutes=30)
        self.assertEqual(inserted_second.parameters["time_window"], [810, 840])

    async def test_today_insertion_respects_previous_night_and_rejects_past_times(self):
        self.now = self.now.replace(hour=1, minute=0)
        sleep = await self.add_goal("睡觉", [1380, 1860], when=self.now - timedelta(days=1))
        with self.assertLogs(level="ERROR"):
            for at in ("01:00", "00:30", "23:45"):
                with self.subTest(time=at), self.assertRaises(ValueError):
                    await self.engine.run_db(self.engine.goal_manager.insert_today_commitment, "不能插入", time=at)
        inserted = await self.engine.run_db(self.engine.goal_manager.insert_today_commitment, "早晨问候", time="07:00")
        self.assertEqual(inserted.parameters["time_window"], [420, 450])
        self.assertIsNotNone(await self.engine.run_db(self.engine.goal_manager.get_goal, sleep))

    async def test_judge_disabled_keeps_distant_dates_and_rejects_past_dates(self):
        self.config.schedule.role_judge_enabled = False
        for description, days in (
            ("大后天18:00一起吃饭", 3), ("四天后18:00一起聊天", 4),
            ("十四天后18:00一起聊天", 14), ("一百零二天后18:00一起聊天", 102),
            ("3650天后18:00一起聊天", 3650),
        ):
            with self.subTest(description=description):
                result = await self.engine.handle_tool("update_schedule", description=description, stream_id="real-private-123")
                self.assertEqual(result["type"], "schedule_committed", result)
                day = (self.now + timedelta(days=days)).date().isoformat()
                pending = await self.engine.run_db(self.engine.goal_manager.get_pending_commitments, day)
                self.assertEqual(pending[0].parameters["time"], "18:00")
        with self.assertLogs(level="ERROR"):
            invalid = await self.engine.handle_tool("update_schedule", description="2026-09-10一起聊天")
        self.assertEqual(invalid["type"], "error", invalid)
        self.assertEqual(self.ctx.calls, [])

    async def test_role_judge_accepts_distant_date_and_infers_long_relative_date(self):
        self.ctx.responses.append(json.dumps({
            "decision": "future", "raw_date": "十四天后", "time": "18:00",
            "title": "一起散步", "duration_minutes": 45,
        }))
        result = await self.engine.handle_tool(
            "update_schedule", description="十四天后18:00一起散步", stream_id="real-private-123",
        )
        self.assertEqual(result["type"], "schedule_committed", result)
        prompt = self.ctx.calls[-1][2]["prompt"]
        self.assertIn("不限制未来天数", prompt)
        day = (self.now + timedelta(days=14)).date().isoformat()
        pending = await self.engine.run_db(self.engine.goal_manager.get_pending_commitments, day)
        self.assertEqual(pending[0].parameters["duration_minutes"], 45)
        self.assertIn(day, {item["date"] for item in self.engine.peek().commitments})
        # 到预约当天才生成并消费约定，保存原请求会话和时长。
        self.now += timedelta(days=14)
        self.ctx.responses.append(json.dumps({"schedule_items": [_schedule_item("一起散步", "18:00", 0.75)]}))
        generated = await self.engine.handle_tool("generate_schedule")
        self.assertEqual(generated["type"], "schedule_generated", generated)
        goals = await self.engine.run_db(self.engine.goal_manager.get_schedule_goals, chat_id="global")
        self.assertEqual(goals[0].parameters["commitment_stream_id"], "real-private-123")
        self.assertEqual(goals[0].parameters["time_window"], [1080, 1125])

    async def test_status_masks_other_stream_commitment_content_and_structured_data(self):
        goal = await self.engine.run_db(
            self.engine.goal_manager.insert_today_commitment,
            "PRIVATE_TITLE", time="13:00", notes="PRIVATE_NOTES", reason="PRIVATE_REASON",
            creator_id="private-owner", target_stream_id="private-source",
        )
        original = goal.to_dict()
        private_markers = ("PRIVATE_TITLE", "PRIVATE_NOTES", "PRIVATE_REASON", "private-owner", "private-source")
        for detailed in (False, True):
            for context in ({}, {"stream_id": "other-group"}, {"chat_id": "other-private"}):
                with self.subTest(detailed=detailed, context=context):
                    result = await self.engine.handle_tool("get_planning_status", detailed=detailed, **context)
                    self.assertEqual(result["type"], "planning_status", result)
                    encoded = json.dumps(result, ensure_ascii=False)
                    for marker in private_markers:
                        self.assertNotIn(marker, encoded)
                    self.assertIn("处理约定", result["content"])
                    self.assertEqual(result["data"][0]["parameters"]["time_window"], [780, 810])
        own = await self.engine.handle_tool("get_planning_status", detailed=True, chat_id="private-source")
        self.assertIn("PRIVATE_TITLE", own["content"])
        self.assertEqual(own["data"][0], original)
        stored = await self.engine.run_db(self.engine.goal_manager.get_goal, goal.goal_id)
        self.assertEqual(stored.to_dict(), original)

    async def test_plan_status_and_image_use_request_stream_privacy(self):
        await self.engine.run_db(
            self.engine.goal_manager.insert_today_commitment,
            "PRIVATE_TITLE", time="13:00", notes="PRIVATE_NOTES", target_stream_id="private-source",
        )
        for stream_id, visible in (("other-group", False), ("private-source", True)):
            with self.subTest(stream_id=stream_id):
                status = await self.engine.handle_command("status", stream_id=stream_id)
                self.assertTrue(status[0], status)
                self.assertEqual("PRIVATE_TITLE" in status[1], visible)
                image = await self.engine.handle_command("list", stream_id=stream_id)
                self.assertTrue(image[0], image)
                html = self.ctx.renders[-1][0]
                self.assertEqual("PRIVATE_TITLE" in html, visible)
                self.assertEqual("PRIVATE_NOTES" in html, visible)

    async def test_update_judge_does_not_receive_other_stream_commitment(self):
        await self.engine.run_db(
            self.engine.goal_manager.insert_today_commitment,
            "PRIVATE_TITLE", time="13:00", notes="PRIVATE_NOTES", target_stream_id="private-source",
        )
        self.ctx.responses.append(json.dumps({"decision": "reject", "reason": "时间冲突"}))
        result = await self.engine.handle_tool(
            "update_schedule", description="今天13:00一起聊天", stream_id="other-group",
        )
        self.assertEqual(result["type"], "schedule_unchanged", result)
        prompt = self.ctx.calls[-1][2]["prompt"]
        self.assertNotIn("PRIVATE_TITLE", prompt)
        self.assertNotIn("PRIVATE_NOTES", prompt)
        self.assertIn("13:00–13:30 处理约定", prompt)

    async def run_activity_patrol(self):
        await self.engine.patrol_proactive()
        task = self.engine._proactive._activity_task
        if task is not None:
            await task

    async def test_schedule_proactive_uses_life_state_and_persists_deduplication(self):
        self.config.schedule.proactive_streams = ["qq:private:123"]
        await self.add_goal("午餐", [720, 780])
        self.engine.update_life_state({"enabled": True, "energy": 65, "hunger": 20, "mood_valence": -0.1,
            "sleep_phase": "awake", "scene": "桌边慢慢喝汤", "current_location": "家里"})
        self.ctx.responses.append(json.dumps({"should_speak": True, "topic": "刚盛好的一碗汤", "motive": "想分享小细节"}))
        await self.run_activity_patrol()
        self.assertEqual(len(self.ctx.proactive), 1)
        request = self.ctx.proactive[0]
        self.assertEqual(request["intent"], "maitrace_schedule")
        self.assertEqual(request["priority"], "low")
        self.assertIn("65", self.ctx.calls[0][2]["prompt"])
        self.assertIn("桌边慢慢喝汤", self.ctx.calls[0][2]["prompt"])
        self.assertIn("Planner", request["reason"])
        await self.run_activity_patrol()
        await self.engine.reload_config()
        await self.run_activity_patrol()
        self.assertEqual(len(self.ctx.proactive), 1)
        self.assertEqual(len(self.ctx.calls), 1)
        self.assertTrue((self.engine.data_dir / "proactive_state.json").is_file())

    async def test_activity_proactive_quiet_busy_low_energy_and_private_owner_skip_model(self):
        self.config.schedule.proactive_streams = ["qq:private:123"]
        goal = await self.add_goal("午餐", [0, 1440])
        manager = self.engine.goal_manager
        for description, kind, energy, hour in (
            ("正在睡觉", "daily_routine", 90, 12), ("工作中", "work", 90, 12),
            ("看书学习", "study", 90, 12), ("午餐", "meal", 20, 12), ("午餐", "meal", 90, 23),
        ):
            await self.engine.run_db(manager.update_goal, goal, name=description, goal_type=kind)
            self.now = self.now.replace(hour=hour)
            self.engine.update_life_state({"enabled": True, "energy": energy, "sleep_phase": "awake"})
            await self.run_activity_patrol()
        self.assertIn(("real-private-123", 0.05), self.ctx.frequencies)
        self.assertEqual(self.ctx.calls, [])
        self.assertEqual(self.ctx.proactive, [])
        self.now = self.now.replace(hour=12)
        self.engine.update_life_state({"enabled": True, "energy": 80}, managed_private_streams={"real-private-123"})
        await self.run_activity_patrol()
        self.assertEqual(self.ctx.calls, [])

    async def test_proactive_reject_and_invalid_decision_do_not_submit_and_only_judge_once(self):
        self.config.schedule.proactive_streams = ["qq:private:123"]
        await self.add_goal("午餐", [720, 780])
        self.ctx.responses.append(json.dumps({"should_speak": False}))
        await self.run_activity_patrol()
        await self.run_activity_patrol()
        self.assertEqual(len(self.ctx.calls), 1)
        self.assertEqual(self.ctx.proactive, [])
        self.now = self.now.replace(hour=14)
        await self.add_goal("午后休息", [840, 900])
        self.ctx.responses.append(json.dumps({"should_speak": "yes", "topic": "午后"}))
        with self.assertLogs(level="ERROR"):
            await self.run_activity_patrol()
        await self.run_activity_patrol()
        self.assertEqual(len(self.ctx.calls), 2)
        self.assertEqual(self.ctx.proactive, [])

    async def test_proactive_cooldown_and_daily_quota_survive_reload(self):
        self.config.schedule.proactive_streams = ["qq:private:123"]
        self.config.schedule.activity_proactive_cooldown_minutes = 180
        for hour in (12, 14, 16, 20):
            self.now = self.now.replace(hour=hour)
            await self.add_goal(f"休息{hour}", [hour * 60, hour * 60 + 60])
            if hour in (12, 16):
                self.ctx.responses.append(json.dumps({"should_speak": True, "topic": "手头小事", "motive": "想聊一聊"}))
            await self.run_activity_patrol()
            await self.engine.reload_config()
        self.assertEqual(len(self.ctx.proactive), 2)
        self.assertEqual(len(self.ctx.calls), 2)

    async def test_activity_change_during_model_work_drops_stale_proactive(self):
        self.config.schedule.proactive_streams = ["qq:private:123"]
        await self.add_goal("午餐", [720, 780])
        entered, release = asyncio.Event(), asyncio.Event()

        async def delayed(*args, **kwargs):
            entered.set()
            await release.wait()
            return True, json.dumps({"should_speak": True, "topic": "汤", "motive": "分享"})

        with patch("MaiTrace.services.schedule.proactive.LLMRunner.generate", side_effect=delayed):
            await self.engine.patrol_proactive()
            await asyncio.wait_for(entered.wait(), 2)
            self.now = self.now.replace(hour=14)
            await self.engine.refresh(force=True)
            release.set()
            await self.engine._proactive._activity_task
        self.assertEqual(self.ctx.proactive, [])

    async def test_unload_cancels_owned_activity_judgment(self):
        self.config.schedule.proactive_streams = ["qq:private:123"]
        await self.add_goal("午餐", [720, 780])
        entered = asyncio.Event()

        async def blocked(*args, **kwargs):
            entered.set()
            await asyncio.Event().wait()

        with patch("MaiTrace.services.schedule.proactive.LLMRunner.generate", side_effect=blocked):
            await self.engine.patrol_proactive()
            await asyncio.wait_for(entered.wait(), 2)
            task = self.engine._proactive._activity_task
            await self.engine.stop()
            self.assertTrue(task.cancelled())
        self.assertEqual(self.ctx.proactive, [])

    async def test_group_whitelist_does_not_match_sender_private_chat(self):
        self.config.schedule.allowed_streams = ["qq:private:123"]
        group = {"stream_id": "real-group-456", "platform": "qq", "group_id": "456", "user_id": "123"}
        denied = await self.engine.handle_tool("get_planning_status", **group)
        self.assertEqual(denied["type"], "error")
        self.config.schedule.allowed_streams = ["qq:group:456"]
        allowed = await self.engine.handle_tool("get_planning_status", **group)
        self.assertEqual(allowed["type"], "planning_status")

    async def test_cancelled_start_closes_database_created_in_worker(self):
        loop = asyncio.get_running_loop()
        opened = asyncio.Event()
        release = threading.Event()
        managers = []

        def delayed_manager(*args, **kwargs):
            manager = GoalManager(*args, **kwargs)
            managers.append(manager)
            loop.call_soon_threadsafe(opened.set)
            if not release.wait(timeout=5):
                manager.close()
                raise AssertionError("测试未释放数据库构造")
            return manager

        other = ScheduleEngine(self.plugin, data_dir=Path(self.temp.name) / "cancel-start")
        with patch("MaiTrace.services.schedule.planner.goal_manager.GoalManager", side_effect=delayed_manager):
            starting = asyncio.create_task(other.start())
            try:
                await asyncio.wait_for(opened.wait(), timeout=2)
                starting.cancel()
                await asyncio.sleep(0)
                self.assertFalse(starting.done())
                release.set()
                with self.assertRaises(asyncio.CancelledError):
                    await asyncio.wait_for(starting, timeout=3)
                self.assertFalse(other.started)
                self.assertTrue(managers[0].db._closed)
                self.assertIsNone(other._worker)
            finally:
                release.set()
                await asyncio.gather(starting, return_exceptions=True)
                await other.stop()

    async def test_cancelled_unload_finishes_closing_and_cancels_active_work(self):
        loop = asyncio.get_running_loop()
        generating, resolving, closing = asyncio.Event(), asyncio.Event(), asyncio.Event()
        blocked = asyncio.Event()
        release = threading.Event()
        self.config.schedule.proactive_streams = ["qq:private:123"]
        await self.add_goal("午餐", [720, 780])
        database = self.engine.goal_manager.db
        original_close = database.close

        async def blocked_llm(*args, **kwargs):
            generating.set()
            await blocked.wait()

        async def blocked_stream(*args, **kwargs):
            resolving.set()
            await blocked.wait()

        def delayed_close():
            loop.call_soon_threadsafe(closing.set)
            if not release.wait(timeout=5):
                raise AssertionError("测试未释放数据库关闭")
            original_close()

        tasks = []
        with patch.object(self.ctx, "call_capability", side_effect=blocked_llm), \
                patch.object(self.ctx.chat, "get_stream_by_user_id", side_effect=blocked_stream), \
                patch.object(database, "close", side_effect=delayed_close):
            try:
                regenerating = asyncio.create_task(self.engine.handle_command("regenerate"))
                patrolling = asyncio.create_task(self.engine.patrol_proactive())
                tasks.extend([regenerating, patrolling])
                await asyncio.wait_for(generating.wait(), timeout=2)
                await asyncio.wait_for(resolving.wait(), timeout=2)
                stopping = asyncio.create_task(self.engine.stop())
                tasks.append(stopping)
                await asyncio.wait_for(closing.wait(), timeout=2)
                stopping.cancel()
                release.set()
                with self.assertRaises(asyncio.CancelledError):
                    await asyncio.wait_for(stopping, timeout=3)
                self.assertTrue(database._closed)
                self.assertTrue(regenerating.cancelled())
                self.assertTrue(patrolling.cancelled())
                self.assertFalse(self.ctx.proactive)
                self.assertFalse(self.engine.started)
            finally:
                release.set()
                for task in tasks:
                    if not task.done():
                        task.cancel()
                await asyncio.gather(*tasks, return_exceptions=True)

    async def test_nested_command_is_tracked_until_reply_finishes(self):
        sending = asyncio.Event()
        blocked = asyncio.Event()

        async def blocked_reply(*args, **kwargs):
            sending.set()
            await blocked.wait()

        with patch.object(self.ctx.send, "text", side_effect=blocked_reply):
            command = asyncio.create_task(self.engine.handle_command("status", stream_id="real-stream"))
            await asyncio.wait_for(sending.wait(), timeout=2)
            await self.engine.stop()
            self.assertTrue(command.cancelled())
            self.assertFalse(self.ctx.texts)


class ScheduleOverlapTests(TestCase):
    def test_intersections_containment_boundaries_and_cross_midnight(self):
        validator = ScheduleSemanticValidator()
        cases = [
            ([('09:00', 2), ('10:00', 1)], True),
            ([('09:00', 4), ('10:00', 1), ('12:00', 1)], True),
            ([('09:00', 2), ('09:00', 1)], True),
            ([('09:00', 2), ('11:00', 1)], False),
            ([('22:30', 2), ('23:30', 1)], True),
            ([('01:30', 1), ('23:00', 2.5)], False),
        ]
        for windows, expected in cases:
            with self.subTest(windows=windows):
                items = [_schedule_item(f"活动{index}", start, duration) for index, (start, duration) in enumerate(windows)]
                self.assertEqual(bool(validator.check_time_overlaps(list(reversed(items)))), expected)
        nested = [_schedule_item("长活动", "09:00", 4), _schedule_item("短活动", "10:00", 1), _schedule_item("下一项", "13:00", 1)]
        self.assertFalse(any("空档" in warning for warning in validator._check_time_continuity(nested)))


if __name__ == "__main__":
    main()
