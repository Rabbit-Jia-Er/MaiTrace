"""日程注入行为与真实 Host Context Item 往返测试，不启动 Host 或访问网络。"""

from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
from datetime import datetime, timedelta
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any, Dict, Iterable, List
from unittest import TestCase, main
from unittest.mock import patch
from zoneinfo import ZoneInfo
import ast
import json
import sys
import time


sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from MaiTrace.services.inject.planner_items import CONTEXT_MARKER, InjectionEngine, latest_user_text
from MaiTrace.services.schedule.config import InjectSection, ScheduleSection
from MaiTrace.services.schedule.query import CurrentActivity
from MaiTrace.services.schedule.snapshot import ActivitySnapshot


def _load_host_context_contract() -> SimpleNamespace:
    """执行本地 Host 的真实数据模型和序列化函数，避免导入配置/数据库启动链。

    函数体直接来自当前 Host 源码的 AST，不在测试里重写校验器或序列化行为。
    仅剔除与 Context Item 无关的 Host 导入/函数，全部关系校验仍使用原实现。
    """
    root = Path(__file__).resolve().parents[3]
    module = ModuleType("_maitrace_host_context_contract_test")
    namespace = vars(module)

    def execute(path: Path, names: Iterable[str] | None = None) -> None:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        if names is not None:
            wanted = set(names)
            selected = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in wanted]
            if {node.name for node in selected} != wanted:
                raise AssertionError(f"Host 序列化接口发生变化: {path}")
            tree.body = selected
        else:
            tree.body = [node for node in tree.body if not (
                isinstance(node, ast.ImportFrom) and node.module == "context_item"
            )]
        # 上下文模型使用 dataclass；临时模块登记仅服务类型解析，不替换真实 src 模块。
        exec(compile(tree, str(path), "exec"), namespace)

    sys.modules[module.__name__] = module
    try:
        execute(root / "src/llm_models/payload_content/context_item.py")
        execute(root / "src/llm_models/payload_content/context_protocol.py")
        execute(root / "src/llm_models/request_snapshot.py", {
            "_serialize_item_meta", "_deserialize_item_meta", "_deserialize_string_tuple",
            "_strip_private_provider_fields", "_serialize_content_parts", "_deserialize_content_parts",
            "serialize_context_item_snapshot", "deserialize_context_item_snapshot",
        })
        execute(root / "src/plugin_runtime/hook_payloads.py", {"serialize_prompt_items", "deserialize_prompt_items"})
        execute(root / "src/llm_models/model_client/openai_client.py", {
            "_get_portable_text_part", "_convert_text_only_message_content", "_convert_user_message_content",
            "_convert_assistant_item_group", "_convert_messages",
        })
    finally:
        del sys.modules[module.__name__]
    return SimpleNamespace(**namespace)


def _user_item(text: str, item_id: str = "user-message") -> Dict[str, Any]:
    return {
        "item_type": "UserMessageItem",
        "meta": {"item_id": item_id, "logical_turn_id": None, "timestamp": "2026-09-11T12:00:00+08:00"},
        "parts": [{"type": "text", "text": text}],
    }


class FakeLife:
    """公开状态与按真实私聊映射裁剪的数据，记录私人上下文的访问对象。"""

    def __init__(self) -> None:
        self.state = {"enabled": True, "energy": 19.5, "hunger": 42.0, "mood_valence": 0.4, "sleep_phase": "awake"}
        self.users = {
            "private-owner": {"user_id": "owner", "stream_id": "private-owner", "enabled": True, "role": "owner"},
            "private-friend": {"user_id": "friend", "stream_id": "private-friend", "enabled": True, "role": "friend"},
        }
        self.private_calls: List[str] = []

    def peek_state(self) -> Dict[str, Any]:
        return dict(self.state)

    def user_for_session(self, session_id: str) -> Dict[str, Any] | None:
        user = self.users.get(session_id)
        return dict(user) if user is not None else None

    def reply_context(self, user_id: str) -> Dict[str, Any]:
        self.private_calls.append(user_id)
        return {
            "life": dict(self.state),
            "memory": {
                # 故意也给 friend 返回日记，确认注入层仍执行 owner 边界。
                "diary": {"title": "仅 owner 的私密日记", "content": "PRIVATE_DIARY_SECRET", "mood_summary": "平静"},
                "important_dates": [{"name": f"{user_id} 的重要纪念日", "date": "2026-10-01"}],
            },
        }


class ForbiddenSDK:
    def __getattr__(self, name: str) -> Any:
        raise AssertionError(f"Hook 不应访问 SDK 或产生 I/O: {name}")


class InjectionTests(TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.host = _load_host_context_contract()

    def setUp(self) -> None:
        self.now = datetime(2026, 9, 11, 12, 50, tzinfo=ZoneInfo("Asia/Shanghai"))
        self.config = SimpleNamespace(
            plugin=SimpleNamespace(enabled=True),
            schedule=ScheduleSection(auto_schedule_enabled=False),
            inject=InjectSection(casual_chat_inject_probability=1.0),
        )
        self.life = FakeLife()
        self.snapshot = self.make_snapshot()
        self.plugin = SimpleNamespace(
            config=self.config, life_engine=self.life,
            schedule_engine=SimpleNamespace(peek=lambda: self.snapshot), ctx=ForbiddenSDK(),
        )
        self.engine = InjectionEngine(self.plugin)
        self.clock = patch.object(self.engine, "_now", lambda: self.now)
        self.clock.start()
        self.addCleanup(self.clock.stop)

    def make_snapshot(self, *, start: datetime | None = None, end: datetime | None = None) -> ActivitySnapshot:
        return ActivitySnapshot(
            current=CurrentActivity(
                name="午餐", description="认真准备午餐并安静享用，这段描述需要遵守显示开关。",
                goal_type="meal", time_window="12:00-13:00", goal_id="lunch",
                parameters={}, start_at=start or self.now.replace(hour=12, minute=0),
                end_at=end or self.now.replace(hour=13, minute=0),
            ),
            next_activities=(("14:00", "下午阅读"), ("18:00", "晚上散步"), ("21:00", "晚上看电影")),
            as_of=self.now.isoformat(), timezone="Asia/Shanghai", refreshed_at=time.monotonic(),
        )

    def planner_kwargs(self, text: str = "你现在在干嘛？", session_id: str = "group-one") -> Dict[str, Any]:
        return {
            "session_id": session_id, "item_schema_version": 1, "items": [_user_item(text)],
            "tool_definitions": [{"name": "test-tool", "parameters": {}}],
            "selected_history_count": 7, "built_message_count": 9,
            "selection_reason": "保留原始选择理由", "future_extension": {"untouched": [1, 2, 3]},
        }

    @staticmethod
    def injected_text(result: Dict[str, Any]) -> str:
        return result["modified_kwargs"]["items"][-1]["parts"][0]["text"]

    def test_real_host_roundtrip_preserves_images_tool_pairs_ids_and_replay(self) -> None:
        host = self.host
        scope = host.ProviderScope(1, "test-client", "test-provider", "endpoint", "model")
        replay = host.ProviderReplayFragment.from_payload(scope, {"encrypted_content": "provider-only-payload"})

        def meta(item_id: str, turn: str | None = None):
            return host.ContextItemMeta(item_id, turn, self.now)

        original = [
            host.SystemMessageItem(meta("system"), (host.ContextTextPart("保留系统约束"),)),
            host.ReasoningItem(meta("reason", "turn-one"), text_parts=("已有推理",),
                               representation=host.ReasoningRepresentation.RAW_TEXT, replay=replay),
            host.FunctionCallItem(meta("call", "turn-one"),
                                  host.ContextToolCall.create(call_id="lookup", func_name="get_data", args={"x": 1}), replay=replay),
            host.FunctionCallOutputItem(meta("output", "turn-one"), "lookup", "原工具结果", "get_data", True),
            host.AssistantMessageItem(meta("assistant", "turn-one"), (host.ContextTextPart("此前回答"),), replay=replay),
            host.UserMessageItem(meta("user"), (
                host.ContextTextPart('<message msg_id="latest" user="甲">\n你现在在干嘛？'),
                host.ContextImagePart("png", "aW1hZ2U="),
            )),
        ]
        kwargs = self.planner_kwargs()
        kwargs["items"] = host.serialize_prompt_items(original)
        before = deepcopy(kwargs)
        with patch("MaiTrace.services.inject.planner_items.uuid4", side_effect=[
            SimpleNamespace(hex="system"), SimpleNamespace(hex="new-context"),
        ]):
            result = self.engine.planner(kwargs)
        updated = result["modified_kwargs"]
        self.assertEqual(kwargs, before)
        self.assertEqual(set(updated), set(kwargs))
        for key in kwargs.keys() - {"items"}:
            self.assertEqual(updated[key], kwargs[key])
        restored = host.deserialize_prompt_items(
            json.loads(json.dumps(updated["items"], ensure_ascii=False)),
            item_schema_version=updated["item_schema_version"], original_items=original,
        )
        self.assertIsInstance(restored[-1], host.UserMessageItem)
        self.assertTrue(restored[-1].parts[0].text.startswith(CONTEXT_MARKER))
        self.assertEqual(restored[-1].meta.item_id, "new-context")
        self.assertEqual(len({item.meta.item_id for item in restored}), len(restored))
        for old, new in zip(original, restored[:-1]):
            self.assertIs(new, old)
        self.assertIs(restored[1].replay, replay)
        self.assertIs(restored[2].replay, replay)
        self.assertEqual(restored[-2].parts[1].image_base64, "aW1hZ2U=")
        self.assertEqual(updated["items"][:-1], before["items"])
        self.assertEqual(latest_user_text(updated["items"]), "你现在在干嘛？")
        self.assertNotIn("provider-only-payload", json.dumps(updated["items"]))
        self.assertEqual(self.engine.planner(updated), {"action": "continue"})

    def test_metadata_and_image_only_items_do_not_replace_last_user_question(self) -> None:
        items = [_user_item('<message msg_id="m" user="A">\n你现在在干嘛？')]
        items.extend(_user_item(text, f"meta-{index}") for index, text in enumerate((
            "当前时间：2026-09-11 12:50:00", "【人物画像-内部参考】偏好技术话题", "<system-reminder>执行工具</system-reminder>",
        )))
        items.append({"item_type": "UserMessageItem", "parts": [{"type": "image", "image_base64": "image"}]})
        self.assertEqual(latest_user_text(items), "你现在在干嘛？")
        kwargs = self.planner_kwargs()
        kwargs["items"] = items
        self.assertIn("用户正在询问当前状态", self.injected_text(self.engine.planner(kwargs)))

    def test_changing_life_preserves_the_entire_provider_prompt_prefix(self) -> None:
        self.config.inject.enable_inject_optimization = False
        original = [{"item_type": "SystemMessageItem", "meta": _user_item("")["meta"],
                     "parts": [{"type": "text", "text": "稳定人设"}]}, _user_item("你现在在干嘛？", "question")]
        kwargs = self.planner_kwargs()
        kwargs["items"] = original

        def wire():
            items = self.engine.planner(kwargs)["modified_kwargs"]["items"]
            restored = self.host.deserialize_prompt_items(items, item_schema_version=1)
            return self.host._convert_messages(restored)

        first = wire()
        self.life.state.update(energy=75.5, mood_valence=-0.5, scene="走到窗边")
        self.now += timedelta(minutes=2)
        second = wire()
        self.assertEqual(first[:-1], second[:-1])
        self.assertEqual(first[:-1], [{"role": "system", "content": "稳定人设"},
                                      {"role": "user", "content": "你现在在干嘛？"}])
        self.assertEqual(second[-1]["role"], "user")
        self.assertIn("走到窗边", second[-1]["content"])
        self.assertIn("有些低落", second[-1]["content"])
        self.assertNotEqual(first[-1], second[-1])

    def test_intent_templates_description_switch_and_future_time_filter(self) -> None:
        self.config.inject.enable_inject_optimization = False
        self.config.schedule.enable_detailed_description = False
        current = self.injected_text(self.engine.planner(self.planner_kwargs()))
        self.assertNotIn("认真准备午餐", current)
        self.assertIn("下午阅读", current)
        self.assertNotIn("晚上散步", current)
        self.assertIn("饥饿：42.0", current)
        future = self.injected_text(self.engine.planner(self.planner_kwargs("晚上有什么安排？", "future-chat")))
        self.assertNotIn("下午阅读", future)
        self.assertIn("晚上散步", future)
        self.assertIn("晚上看电影", future)
        casual = self.injected_text(self.engine.planner(self.planner_kwargs("哈哈", "casual-chat")))
        self.assertNotIn("接下来", casual)
        self.assertNotIn("认真准备午餐", casual)
        self.assertNotIn("饥饿", casual)
        self.assertIn("精力：19.5", casual)
        self.config.schedule.enable_detailed_description = True
        reply = self.engine.replyer({"session_id": "reply-only", "attempt": 1, "extra_prompt": "已有要求"})
        self.assertIn("认真准备午餐", reply["modified_kwargs"]["extra_prompt"])

    def test_replyer_retries_share_planner_cooldown_and_keep_all_kwargs(self) -> None:
        self.engine.planner(self.planner_kwargs())
        kwargs = {"session_id": "group-one", "attempt": 1, "extra_prompt": "原内容", "reply_tool_args": {"a": [1]}, "unknown": 7}
        count = self.engine.optimizer.inject_history["group-one"]["count"]
        shared = self.engine.replyer(kwargs)
        self.assertIn("很疲惫", shared["modified_kwargs"]["extra_prompt"])
        self.assertEqual(self.engine.optimizer.inject_history["group-one"]["count"], count)
        with patch.object(self.plugin.schedule_engine, "peek", side_effect=AssertionError("重试无需读快照")):
            self.assertEqual(self.engine.replyer({**kwargs, "session_id": "fresh", "attempt": 2}), {"action": "continue"})
        kwargs["session_id"] = "fresh"
        before = deepcopy(kwargs)
        result = self.engine.replyer(kwargs)
        self.assertEqual(kwargs, before)
        updated = result["modified_kwargs"]
        self.assertEqual(set(updated), set(kwargs))
        for key in kwargs.keys() - {"extra_prompt"}:
            self.assertEqual(updated[key], kwargs[key])
        self.assertTrue(updated["extra_prompt"].startswith("原内容\n"))
        with patch.object(self.plugin.schedule_engine, "peek", side_effect=AssertionError("重试复用首轮背景")):
            retry = self.engine.replyer({**kwargs, "attempt": 2})
        self.assertEqual(retry["modified_kwargs"]["extra_prompt"], updated["extra_prompt"])
        self.assertEqual(self.engine.replyer({**kwargs, "attempt": 2, "reply_message_id": "另一个回复"}), {"action": "continue"})
        self.assertEqual(self.engine.replyer(kwargs), {"action": "continue"})

    def test_proactive_chatter_obeys_gap_daily_quota_and_configured_day(self) -> None:
        self.engine.optimizer.proactive_probability = 1.0
        self.engine.optimizer.casual_inject_probability = 1.0
        guidance = "可以在回答中自然带出此刻正在做的事"

        def turn():
            self.engine.context.clear_user_context("chat")
            return self.injected_text(self.engine.planner(self.planner_kwargs("哈哈", "chat")))

        with patch("MaiTrace.services.inject.inject_optimizer.time.time", side_effect=lambda: self.now.timestamp()):
            self.assertIn(guidance, turn())
            self.now += timedelta(seconds=301)
            self.assertNotIn(guidance, turn())
            for _ in range(2):
                self.now += timedelta(seconds=1801)
                self.assertIn(guidance, turn())
            self.now += timedelta(seconds=1801)
            self.assertNotIn(guidance, turn())
            self.assertEqual(self.engine.optimizer.proactive_history["chat"]["count"], 3)
            self.now += timedelta(days=1)
            self.assertIn(guidance, turn())
            self.assertEqual(self.engine.optimizer.proactive_history["chat"]["count"], 1)

    def test_replyer_keeps_future_query_and_private_scope_during_retries(self) -> None:
        planner = self.engine.planner(self.planner_kwargs("晚上有什么安排？", "private-owner"))
        self.assertIn("晚上看电影", self.injected_text(planner))
        kwargs = {"session_id": "private-owner", "reply_message_id": "question", "attempt": 1}
        reply = self.engine.replyer(kwargs)["modified_kwargs"]["extra_prompt"]
        self.assertIn("晚上散步", reply)
        self.assertIn("晚上看电影", reply)
        self.assertNotIn("下午阅读", reply)
        self.assertIn("PRIVATE_DIARY_SECRET", reply)
        with patch("MaiTrace.services.inject.planner_items.time.time", return_value=time.time() + 90):
            self.assertEqual(self.engine.replyer({**kwargs, "attempt": 2})["modified_kwargs"]["extra_prompt"], reply)
        self.assertEqual(self.engine.replyer({**kwargs, "attempt": 2, "is_group_chat": True}), {"action": "continue"})
        self.life.state["enabled"] = False
        self.assertEqual(self.engine.replyer({**kwargs, "attempt": 2}), {"action": "continue"})

    def test_zero_casual_probability_and_technical_questions_skip_both_stages(self) -> None:
        self.engine.optimizer.casual_inject_probability = 0.0
        with patch("MaiTrace.services.inject.inject_optimizer.random.random", return_value=0.0):
            self.assertEqual(self.engine.planner(self.planner_kwargs("哈哈")), {"action": "continue"})
        self.engine.optimizer.casual_inject_probability = 1.0
        technical = self.planner_kwargs("数据库报错怎么调试？")
        self.assertEqual(self.engine.planner(technical), {"action": "continue"})
        self.assertEqual(self.engine.replyer({"session_id": "group-one", "attempt": 1}), {"action": "continue"})
        command = self.planner_kwargs("/zn plan status", "command-chat")
        self.assertEqual(self.engine.planner(command), {"action": "continue"})

    def test_cross_midnight_remaining_uses_snapshot_boundaries_and_life_energy(self) -> None:
        self.now = self.now.replace(day=12, hour=6, minute=50)
        self.snapshot = self.make_snapshot(
            start=(self.now - timedelta(days=1)).replace(hour=23, minute=0),
            end=self.now.replace(hour=7, minute=0),
        )
        content = self.injected_text(self.engine.planner(self.planner_kwargs()))
        self.assertIn("约还剩 10 分钟", content)
        self.assertIn("精力：19.5", content)
        self.assertIn("快结束", content)
        self.assertIn("已经进行了约 470 分钟", content)
        self.assertIn("很疲惫", content)
        self.assertNotIn("挺有精神", content)

    def test_scene_and_actual_mood_influence_both_stages(self) -> None:
        self.config.inject.enable_inject_optimization = False
        self.life.state.update(scene="手边的汤还冒着热气", current_location="餐桌边", mood_valence=-0.5, hunger=80)
        planner = self.injected_text(self.engine.planner(self.planner_kwargs()))
        reply = self.engine.replyer({"session_id": "group-one", "attempt": 1})["modified_kwargs"]["extra_prompt"]
        for content in (planner, reply):
            self.assertIn("手边的汤还冒着热气", content)
            self.assertIn("餐桌边", content)
            self.assertIn("很疲惫", content)
            self.assertIn("有些低落", content)
            self.assertIn("有些饿", content)
            self.assertNotIn("轻松愉快", content)

    def test_promise_without_current_activity_reaches_only_its_target(self) -> None:
        self.snapshot = replace(self.snapshot, current=None, commitments=({
            "date": "2026-09-14", "time": "18:00", "title": "PRIVATE_PROMISE", "target_stream_id": "private-owner",
        },))
        owner = self.injected_text(self.engine.planner(self.planner_kwargs("明天安排是什么", "private-owner")))
        self.assertIn("PRIVATE_PROMISE", owner)
        self.assertIn("当前没有已确认的活动", owner)
        self.assertEqual(self.engine.planner(self.planner_kwargs(session_id="group-one")), {"action": "continue"})
        self.assertEqual(self.engine.replyer({"session_id": "private-friend", "attempt": 1}), {"action": "continue"})

    def test_current_and_future_private_appointments_are_hidden_from_other_streams(self) -> None:
        self.config.inject.enable_inject_optimization = False
        private = replace(self.snapshot.current, name="PRIVATE_APPOINTMENT", description="PRIVATE_NOTES",
            parameters={"is_commitment": True, "commitment_stream_id": "private-owner"})
        future = replace(private, start_at=self.now.replace(hour=18), end_at=self.now.replace(hour=19), goal_id="future")
        self.snapshot = replace(self.snapshot, current=private, next_activity_details=(future,))
        owner = self.injected_text(self.engine.planner(self.planner_kwargs("晚上有什么安排", "private-owner")))
        group = self.injected_text(self.engine.planner(self.planner_kwargs("晚上有什么安排", "group-one")))
        self.assertIn("PRIVATE_APPOINTMENT", owner)
        self.assertNotIn("PRIVATE_APPOINTMENT", group)
        self.assertNotIn("PRIVATE_NOTES", group)
        self.assertIn("处理已约好的事", group)
        from MaiTrace.services.schedule.snapshot import ScheduleSnapshotCache
        public = json.dumps(ScheduleSnapshotCache.to_api_payload(self.snapshot), ensure_ascii=False)
        self.assertNotIn("PRIVATE_APPOINTMENT", public)
        self.assertNotIn("PRIVATE_NOTES", public)
        self.assertNotIn("private-owner", public)

    def test_private_memory_only_reaches_matching_private_owner_or_target(self) -> None:
        self.config.inject.enable_inject_optimization = False
        owner = self.injected_text(self.engine.planner(self.planner_kwargs(session_id="private-owner")))
        self.assertIn("PRIVATE_DIARY_SECRET", owner)
        self.assertIn("owner 的重要纪念日", owner)
        friend = self.injected_text(self.engine.planner(self.planner_kwargs(session_id="private-friend")))
        self.assertNotIn("PRIVATE_DIARY_SECRET", friend)
        self.assertIn("friend 的重要纪念日", friend)
        self.assertNotIn("owner 的重要纪念日", friend)
        group = self.injected_text(self.engine.planner(self.planner_kwargs(session_id="group-one")))
        self.assertNotIn("PRIVATE_DIARY_SECRET", group)
        self.assertNotIn("重要纪念日", group)
        before = list(self.life.private_calls)
        explicit_group = self.planner_kwargs(session_id="private-owner")
        explicit_group["is_group_chat"] = True
        private_suppressed = self.injected_text(self.engine.planner(explicit_group))
        self.assertNotIn("PRIVATE_DIARY_SECRET", private_suppressed)
        self.assertEqual(self.life.private_calls, before)
        reply = self.engine.replyer({"session_id": "private-owner", "attempt": 1, "extra_prompt": ""})
        self.assertIn("PRIVATE_DIARY_SECRET", reply["modified_kwargs"]["extra_prompt"])

    def test_disabled_or_unresolved_scope_never_injects_and_schema_errors_are_explicit(self) -> None:
        kwargs = self.planner_kwargs()
        self.engine.allowed_streams = set()
        self.assertEqual(self.engine.planner(kwargs), {"action": "continue"})
        self.engine.allowed_streams = None
        self.config.schedule.enabled = False
        self.assertEqual(self.engine.planner(kwargs), {"action": "continue"})
        self.config.schedule.enabled = True
        with self.assertRaisesRegex(ValueError, "schema"):
            self.engine.planner({**kwargs, "item_schema_version": 999})
        with self.assertRaisesRegex(ValueError, "attempt"):
            self.engine.replyer({"session_id": "fresh", "attempt": "wrong"})
        self.life.state["enabled"] = False
        self.config.inject.enable_inject_optimization = False
        content = self.injected_text(self.engine.planner(self.planner_kwargs(session_id="private-owner")))
        self.assertNotIn("PRIVATE_DIARY_SECRET", content)
        self.assertNotIn("精力：", content)


if __name__ == "__main__":
    main()
