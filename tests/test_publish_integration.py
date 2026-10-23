"""发布链路的离线回归：真实 SDK 解包、LLM 包装、生图收集与 Qzone 状态机。

只替换 RPC、Cookie 获取、正文上下文构造和 HTTP 传输；不会加载实际配置或 data。
"""

from __future__ import annotations

from dataclasses import dataclass
from maibot_sdk.context import PluginContext
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, Iterator, List, Type
from unittest.mock import AsyncMock
import asyncio
import base64
import httpx
import pytest
import sys


sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from MaiTrace.config import MaiTracePluginConfig
from MaiTrace.services.prompts import build_send_prompt
from MaiTrace.handlers import apis
from MaiTrace.services import feed_image, feed_publish, qzone_publisher, routine
from MaiTrace.services.activity import ActivityInfo, ActivityType
from MaiTrace.services.limits import publication_budget_seconds
from MaiTrace.services.qzone_api import PublicationState, PublicationUncertainError, QzoneAPI


PLUGIN_ID = "Rabbit-Jia-Er.MaiTrace"
ART_PLUGIN_ID = "1021143806.mais_art_journal"
PNG_BASE64 = (
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aL1c"
    "AAAAASUVORK5CYII="
)
PNG_BYTES = base64.b64decode(PNG_BASE64)
NOW = 1_800_000_000.0


def image_result(**changes: Any) -> Dict[str, Any]:
    result = {
        "success": True, "image_base64": PNG_BASE64, "model_id": "offline-model",
        "size": "1x1", "is_img2img": False, "error": "", "selfie_style": "photo", "outfit": "",
    }
    result.update(changes)
    return result


class FakeRPC:
    """返回 Host 的外层响应，让真实 PluginContext 完成 capability 解包。"""

    def __init__(self, events: List[str]) -> None:
        self.events = events
        self.calls: List[Dict[str, Any]] = []
        self.image_results: List[Any] = []
        self.llm_responses: List[str] = []
        self.unexpected_calls: List[str] = []
        self.global_config: Dict[str, Any] = {
            "bot.qq_account": "999",
            "bot.nickname": "离线麦麦",
            "bot.alias_names": [],
            "personality.personality": "喜欢记录日常的机器人",
            "personality.reply_style": "自然简洁",
            "personality.multiple_reply_style": [],
            "personality.multiple_probability": 0.0,
        }

    @property
    def image_calls(self) -> List[Dict[str, Any]]:
        return [call for call in self.calls if call["capability"] == "api.call"]

    @property
    def llm_calls(self) -> List[Dict[str, Any]]:
        return [call for call in self.calls if call["capability"] == "llm.generate"]

    async def __call__(
        self, method: str, plugin_id: str, payload: Dict[str, Any], **kwargs: Any,
    ) -> Any:
        assert method == "cap.call"
        assert plugin_id == PLUGIN_ID
        capability, args = payload["capability"], payload["args"]
        self.calls.append({"capability": capability, "args": args, **kwargs})
        if capability == "config.get" and args["key"] in self.global_config:
            return {"success": True, "value": self.global_config[args["key"]]}
        if capability == "api.list":
            return {"success": True, "apis": []}
        if capability == "config.get_plugin" and args["plugin_name"] == ART_PLUGIN_ID:
            return {"success": True, "value": {"selfie": {
                "enabled": True, "prompt_prefix": "silver hair", "reference_image_path": "",
            }}}
        if capability == "llm.generate" and self.llm_responses:
            self.events.append("llm")
            return {"success": True, "response": self.llm_responses.pop(0)}
        if capability == "api.call" and self.image_results:
            assert args["api_name"] == f"{ART_PLUGIN_ID}.generate_image"
            assert args["version"] == "1"
            number = len(self.image_calls)
            self.events.append(f"generate:{number}")
            result = self.image_results.pop(0)
            if isinstance(result, BaseException):
                raise result
            if callable(result):
                result = await result(args["args"])
            self.events.append(f"generated:{number}")
            return {"success": True, "result": result}
        # 即使业务层捕获 AssertionError，fixture 收尾时也会报告越界调用。
        self.unexpected_calls.append(capability)
        raise AssertionError(f"测试没有授权此 RPC：{capability}")


class FakeQzoneTransport:
    """保留真正的图片上传解析和发布状态机，仅在 HTTP 边界返回内存响应。"""

    def __init__(self, events: List[str]) -> None:
        self.events = events
        self.calls: List[Dict[str, Any]] = []
        self.upload_results: List[Any] = []
        self.publish_results: List[Any] = []

    @property
    def uploads(self) -> List[Dict[str, Any]]:
        return [call for call in self.calls if call["url"] == QzoneAPI.UPLOAD_IMAGE_URL]

    @property
    def publications(self) -> List[Dict[str, Any]]:
        return [call for call in self.calls if call["url"] == QzoneAPI.EMOTION_PUBLISH_URL]

    async def __call__(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        assert method == "POST"
        request = {"method": method, "url": url, **kwargs}
        self.calls.append(request)
        if url == QzoneAPI.UPLOAD_IMAGE_URL:
            number = len(self.uploads)
            self.events.append(f"upload:{number}")
            result = self.upload_results.pop(0) if self.upload_results else httpx.Response(200, json={
                "ret": 0, "data": {
                    "url": f"https://offline.invalid/image?x=1&bo=image-{number}",
                    "albumid": "offline", "lloc": "l", "sloc": "s", "type": 1, "height": 1, "width": 1,
                },
            })
            completed_event = f"uploaded:{number}"
        elif url == QzoneAPI.EMOTION_PUBLISH_URL:
            number = len(self.publications)
            self.events.append(f"publish:{number}")
            result = self.publish_results.pop(0) if self.publish_results else httpx.Response(
                200, json={"code": 0, "tid": f"offline-tid-{number}"},
            )
            completed_event = f"published:{number}"
        else:
            raise AssertionError(f"测试没有授权此 Qzone 请求：{url}")
        if isinstance(result, BaseException):
            raise result
        if callable(result):
            result = await result(request)
        assert isinstance(result, httpx.Response)
        self.events.append(completed_event)
        return result


@dataclass
class PublishHarness:
    plugin: SimpleNamespace
    rpc: FakeRPC
    qzone: QzoneAPI
    transport: FakeQzoneTransport
    events: List[str]
    retry_sleep: AsyncMock
    custom_content: AsyncMock
    api_cookie_refresh: AsyncMock


@pytest.fixture(autouse=True)
def forbid_real_http(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    attempts: List[str] = []

    def reject_request(*args: Any, **kwargs: Any) -> None:
        attempts.append("HTTP")
        raise AssertionError("离线发布回归禁止真实 HTTP")

    async def reject_async_request(*args: Any, **kwargs: Any) -> None:
        reject_request(*args, **kwargs)

    monkeypatch.setattr(httpx.Client, "request", reject_request)
    monkeypatch.setattr(httpx.Client, "send", reject_request)
    monkeypatch.setattr(httpx.AsyncClient, "request", reject_async_request)
    monkeypatch.setattr(httpx.AsyncClient, "send", reject_async_request)
    yield
    assert not attempts, "业务层捕获异常后仍应报告真实 HTTP 尝试"


@pytest.fixture
def publishing(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[PublishHarness]:
    config = MaiTracePluginConfig()
    config.image.enable_image = True
    config.image.image_mode = "only_ai"
    config.image.image_number = 1
    config.image.image_retry = 2
    config.image.require_image = True
    config.image.clear_image = True
    config.image.pic_plugin_model = "offline-model"
    config.image.selfie_style = "photo"
    config.image.ai_content_mode = "selfie"
    config.routine.respect_silent_hours = False
    config.routine.post_blocked_activities = []
    config.routine.max_post_chance = 1.0
    config.routine.post_cooldown_minutes = 60
    config.persona.use_multiple_reply_style = False
    events: List[str] = []
    rpc = FakeRPC(events)
    plugin = SimpleNamespace(
        config=config, ctx=PluginContext(PLUGIN_ID, rpc_call=rpc), schedule_engine=None, life_engine=None,
    )
    from MaiTrace.services.persistence import PluginPersistence
    plugin.persistence = PluginPersistence(tmp_path)
    qzone = QzoneAPI({"p_skey": "offline-test-cookie"}, "999")
    transport = FakeQzoneTransport(events)
    monkeypatch.setattr(qzone, "_do", transport)
    monkeypatch.setattr(qzone_publisher, "create_qzone_api", lambda uin, **kwargs: qzone)
    api_cookie_refresh = AsyncMock(return_value=True)
    monkeypatch.setattr(qzone_publisher, "renew_cookies", api_cookie_refresh)

    async def build_prompt(
        plugin: Any, topic: str, personality: str, style: str, **kwargs: Any,
    ) -> str:
        return f"发布主题：{topic}\n当前活动：{kwargs['current_activity']}\n人格：{personality}"

    # 历史说说与聊天整理有各自测试；这里保留真正的正文 LLM 请求和 SDK。
    monkeypatch.setattr(feed_publish, "build_send_prompt", build_prompt)
    custom_content = AsyncMock(return_value="PRIVATE_SENTINEL")
    monkeypatch.setattr(feed_publish, "_resolve_custom_content", custom_content)
    retry_sleep = AsyncMock()
    # 只替换生图模块的等待，保留测试和 LLMRunner 所需的真实 asyncio。
    monkeypatch.setattr(feed_image, "asyncio", SimpleNamespace(sleep=retry_sleep, to_thread=asyncio.to_thread))
    monkeypatch.setattr(routine, "time", SimpleNamespace(time=lambda: NOW))
    yield PublishHarness(plugin, rpc, qzone, transport, events, retry_sleep, custom_content, api_cookie_refresh)
    assert not rpc.unexpected_calls


def claim(token: str, *, topic: str = "午后散步", instruction: str = "仅分享可公开的日常") -> Dict[str, Any]:
    return {"id": "offline-material", "claim_token": token, "topic": topic, "instruction": instruction}


def test_random_image_content_is_per_image_and_retry_keeps_selected_mode(
    publishing: PublishHarness, monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    h = publishing
    h.plugin.config.image.ai_content_mode = "random"
    h.plugin.config.image.image_number = 2
    reference = tmp_path / "reference.png"
    reference.write_bytes(b"character-reference")
    draw = iter([0.1, 0.9])
    monkeypatch.setattr(feed_image.random, "random", lambda: next(draw))
    h.rpc.image_results = [image_result(success=False, image_base64="", error="重试"), image_result(), image_result()]
    images = asyncio.run(feed_image.collect_images_for_feed(h.plugin, "餐桌上的汤", reference_image_path=str(reference),
        activity_type="eating", outfit="居家服"))
    assert len(images) == 2
    args = [call["args"]["args"] for call in h.rpc.image_calls]
    assert [item["selfie_mode"] for item in args] == [True, True, False]
    assert args[0]["input_image_base64"] == args[1]["input_image_base64"]
    assert args[0]["outfit"] == "居家服"
    assert not {"input_image_base64", "selfie_style", "outfit", "activity_type"}.intersection(args[2])
    assert h.retry_sleep.await_count == 1


def test_ordinary_drawing_does_not_read_character_reference(publishing: PublishHarness, tmp_path: Path) -> None:
    h = publishing
    h.plugin.config.image.ai_content_mode = "illustration"
    h.rpc.image_results = [image_result()]
    images = asyncio.run(feed_image.collect_images_for_feed(h.plugin, "一碗汤的速写", reference_image_path=str(tmp_path / "missing.png")))
    assert len(images) == 1
    args = h.rpc.image_calls[0]["args"]["args"]
    assert args["selfie_mode"] is False
    assert "input_image_base64" not in args


def life_source(claims: List[Any], *, confirm: bool = True) -> SimpleNamespace:
    """素材存储用内存替身；租约选择由测试显式给定，消费由真实 Routine 执行。"""
    return SimpleNamespace(
        claim_public_opportunity=AsyncMock(side_effect=claims),
        confirm=AsyncMock(return_value=confirm),
        release=AsyncMock(return_value=True),
        quarantine=AsyncMock(return_value=True),
    )


@pytest.mark.parametrize("failed_image", range(4))
@pytest.mark.parametrize("retries", [0, 2])
def test_each_required_image_failure_stops_before_qzone(
    publishing: PublishHarness, failed_image: int, retries: int,
) -> None:
    h = publishing
    h.plugin.config.image.image_number = 4
    h.plugin.config.image.image_retry = retries
    h.rpc.llm_responses = ["离线生成的正文"]
    h.rpc.image_results = [image_result() for _ in range(failed_image)] + [
        image_result(success=False, image_base64="", error="离线模拟生图失败") for _ in range(retries + 1)
    ]
    state = PublicationState()

    success, error = asyncio.run(feed_publish.send_feed(h.plugin, "午后", publication=state))

    assert not success
    assert f"第 {failed_image + 1} 张" in error
    assert "未发送" in error
    assert not h.transport.calls
    assert state.stage == "preparing"
    assert not state.uncertain
    assert len(h.rpc.image_calls) == failed_image + retries + 1
    assert h.retry_sleep.await_count == retries
    assert all(call.args == (5,) for call in h.retry_sleep.await_args_list)
    for call in h.rpc.image_calls:
        assert call["timeout_ms"] == 185_000
        assert "timeout_ms" not in call["args"]
        image_args = call["args"]["args"]
        assert image_args["selfie_mode"] is True
        assert image_args["use_cache"] is False
        assert image_args["model_id"] == "offline-model"


@pytest.mark.parametrize("result", [
    image_result(image_base64="not valid base64!"),
    image_result(image_base64=""),
    None,
    TimeoutError("离线模拟生图 RPC 超时"),
], ids=["invalid-base64", "empty-image", "invalid-payload", "rpc-timeout"])
def test_invalid_image_response_cannot_publish_text_only(publishing: PublishHarness, result: Any) -> None:
    h = publishing
    h.plugin.config.image.image_retry = 0
    h.rpc.llm_responses = ["离线正文"]
    h.rpc.image_results = [result]

    success, error = asyncio.run(feed_publish.send_feed(h.plugin, "午后"))

    assert not success
    assert "未发送" in error
    assert not h.transport.calls
    assert len(h.rpc.image_calls) == 1


def test_all_images_complete_before_any_qzone_upload_or_publish(publishing: PublishHarness) -> None:
    h = publishing
    h.plugin.config.image.image_number = 3
    h.rpc.llm_responses = ["三张图片都准备好后发布"]
    state = PublicationState()

    async def scenario() -> None:
        started, finish = asyncio.Event(), asyncio.Event()

        async def delayed_image(args: Dict[str, Any]) -> Dict[str, Any]:
            started.set()
            await finish.wait()
            return image_result()

        h.rpc.image_results = [image_result(), delayed_image, image_result()]
        task = asyncio.create_task(feed_publish.send_feed(h.plugin, "午后", publication=state))
        try:
            await asyncio.wait_for(started.wait(), timeout=2)
            assert len(h.rpc.image_calls) == 2
            assert not h.transport.calls
            assert not task.done()
        finally:
            finish.set()
            success, _ = await task
        assert success

    asyncio.run(scenario())

    assert state.stage == "confirmed"
    assert state.tid == "offline-tid-1"
    assert h.events.index("generated:3") < h.events.index("upload:1")
    assert h.events.index("uploaded:3") < h.events.index("publish:1")
    assert len(h.transport.uploads) == 3
    assert len(h.transport.publications) == 1
    assert all(call["timeout"] == 60 for call in h.transport.uploads)
    assert h.transport.publications[0]["data"]["pic_bo"] == "image-1,image-2,image-3"


@pytest.mark.parametrize("image_count", [0, 2])
def test_send_feed_api_uses_only_caller_content_and_images(publishing: PublishHarness, image_count: int) -> None:
    h = publishing
    # 即使自动发布要求 AI 配图且没有模型，直发 API 仍应只使用调用方的图片。
    h.plugin.config.image.pic_plugin_model = ""
    supplied = [PNG_BASE64] * image_count

    result = asyncio.run(apis.send_feed_api(h.plugin, "调用方准备好的正文", supplied))

    assert result["result"] is True
    assert not h.rpc.image_calls
    assert not h.rpc.llm_calls
    assert len(h.transport.uploads) == image_count
    assert [call["data"]["picfile"] for call in h.transport.uploads] == supplied
    assert len(h.transport.publications) == 1
    assert h.transport.publications[0]["data"]["con"] == "调用方准备好的正文"


@pytest.mark.parametrize("invalid_image", ["", "%%%", "a"])
def test_send_feed_api_rejects_invalid_image_before_cookie_lookup(
    publishing: PublishHarness, invalid_image: str,
) -> None:
    h = publishing

    result = asyncio.run(apis.send_feed_api(h.plugin, "调用方正文", [PNG_BASE64, invalid_image]))

    assert result["result"] is False
    assert "images[1]" in result["message"]
    assert not h.rpc.calls
    assert not h.transport.calls
    h.api_cookie_refresh.assert_not_awaited()


@pytest.mark.parametrize(("goal_type", "expected"), [
    ("meal", "eating"), ("study", "studying"), ("learn_topic", "studying"),
    ("exercise", "exercising"), ("entertainment", "hobby"), ("social_maintenance", "socializing"),
    ("health_check", "self_care"), ("free_time", "relaxing"),
])
def test_manual_topic_converts_schedule_types_for_art_api(
    publishing: PublishHarness, goal_type: str, expected: str,
) -> None:
    h = publishing
    h.plugin.schedule_engine = SimpleNamespace(current_activity_payload=lambda: {
        "has_activity": True,
        "activity": {"goal_type": goal_type, "description": "当前日程场景", "outfit": "蓝色外套"},
    })
    h.rpc.llm_responses = ["离线正文"]
    h.rpc.image_results = [image_result()]

    result = asyncio.run(apis.publish_topic_api(h.plugin, "手动输入的主题"))

    assert result["result"] is True
    args = h.rpc.image_calls[0]["args"]["args"]
    assert args["activity_type"] == expected
    assert args["activity_description"] == "当前日程场景"
    assert args["outfit"] == "蓝色外套"


def test_explicit_activity_type_is_normalized_before_art_rpc(publishing: PublishHarness) -> None:
    h = publishing
    h.rpc.llm_responses = ["离线正文"]
    h.rpc.image_results = [image_result()]

    result = asyncio.run(apis.publish_topic_api(
        h.plugin, "手动主题", current_activity="日程场景", activity_type="meal", outfit="日常服装",
    ))

    assert result["result"] is True
    assert h.rpc.image_calls[0]["args"]["args"]["activity_type"] == "eating"


@pytest.mark.parametrize("outfit", ["", "显式运动服"])
@pytest.mark.parametrize("same_activity", [True, False])
def test_selfie_receives_real_time_location_scene_and_outfit_priority(
    publishing: PublishHarness, outfit: str, same_activity: bool,
) -> None:
    h = publishing
    h.plugin.schedule_engine = SimpleNamespace(current_activity_payload=lambda: {
        "has_activity": True, "as_of": "2026-10-23T18:30:00+08:00", "timezone": "Asia/Shanghai",
        "activity": {"description": "晚饭后散步", "goal_type": "exercise", "time_window": "18:00-19:00",
                     "parameters": {"outfit": "蓝色外套", "location": "公园"}},
    })
    h.plugin.life_engine = SimpleNamespace(peek_state=lambda: {
        "enabled": True, "current_activity": "晚饭后散步" if same_activity else "午餐",
        "scene": "树下吹来微凉的晚风", "current_location": "旧地点",
    })
    h.rpc.llm_responses = ["散步正文"]
    h.rpc.image_results = [image_result()]

    assert asyncio.run(apis.publish_topic_api(h.plugin, "散步", outfit=outfit))["result"]
    args = h.rpc.image_calls[0]["args"]["args"]
    assert args["activity_location"] == "公园"
    assert args["activity_time_window"] == "18:00-19:00"
    assert args["activity_as_of"] == "2026-10-23T18:30:00+08:00"
    assert args["activity_timezone"] == "Asia/Shanghai"
    assert args["outfit"] == (outfit or "蓝色外套")
    assert args.get("activity_scene", "") == ("树下吹来微凉的晚风" if same_activity else "")


def test_empty_selfie_reference_stops_before_any_image_or_publication(
    publishing: PublishHarness, tmp_path: Path,
) -> None:
    reference = tmp_path / "empty-reference.png"
    reference.write_bytes(b"")
    with pytest.raises(feed_image.ImageGenerationError, match="参考图为空"):
        asyncio.run(feed_image.collect_images_for_feed(publishing.plugin, "自拍", reference_image_path=str(reference)))
    assert not publishing.rpc.image_calls
    assert not publishing.transport.publications


def test_routine_releases_failed_material_then_confirms_success_without_cooldown(publishing: PublishHarness) -> None:
    h = publishing
    h.plugin.config.image.image_retry = 1
    life = life_source([claim("first-lease"), claim("second-lease")])
    h.plugin.life_engine = life
    runner = routine.RoutineRunner(h.plugin)
    activity = ActivityInfo(ActivityType.RELAXING, "午后休息")
    h.rpc.llm_responses = ["是|适合分享", "第一轮正文", "是|适合分享", "第二轮正文"]
    h.rpc.image_results = [image_result(success=False, error="失败")] * 2 + [image_result()]

    async def scenario() -> None:
        await runner._try_post_feed(activity)
        assert runner.last_post_time == 0
        assert not h.transport.publications
        assert life.release.await_args.args == ("first-lease",)
        life.confirm.assert_not_awaited()
        life.quarantine.assert_not_awaited()
        await runner._try_post_feed(activity)

    asyncio.run(scenario())

    assert runner.last_post_time == NOW
    life.confirm.assert_awaited_once_with("second-lease", receipt="offline-tid-1")
    life.quarantine.assert_not_awaited()
    assert life.release.await_count == 1
    assert len(h.transport.publications) == 1
    assert [entry["executed"] for entry in runner.get_decision_history()] == [False, True]
    for call in life.claim_public_opportunity.await_args_list:
        assert call.kwargs["consumer"] == "qzone"
        assert call.kwargs["lease_seconds"] >= publication_budget_seconds(h.plugin.config) + 120


def test_routine_uncertain_post_quarantines_material_and_blocks_immediate_retry(publishing: PublishHarness) -> None:
    h = publishing
    life = life_source([claim("uncertain-lease"), None])
    h.plugin.life_engine = life
    runner = routine.RoutineRunner(h.plugin)
    activity = ActivityInfo(ActivityType.RELAXING, "午后休息")
    h.rpc.llm_responses = ["是|适合分享", "公开正文"]
    h.rpc.image_results = [image_result()]
    h.transport.publish_results = [httpx.ReadTimeout("发布 POST 已发出，响应超时")]

    async def scenario() -> None:
        await runner._try_post_feed(activity)
        calls_before = len(h.rpc.calls)
        assert runner.last_post_time == NOW
        assert life.quarantine.await_args.args == ("uncertain-lease",)
        await runner._try_post_feed(activity)
        assert len(h.rpc.calls) == calls_before

    asyncio.run(scenario())

    life.quarantine.assert_awaited_once()
    life.release.assert_not_awaited()
    life.confirm.assert_not_awaited()
    assert len(h.transport.publications) == 1
    assert runner.get_decision_history()[-1]["cooldown_skipped"] is True


@pytest.mark.parametrize("cancel_stage", ["image", "upload", "publish"])
def test_routine_cancellation_uses_publication_stage_for_lease_cleanup(
    publishing: PublishHarness, cancel_stage: str,
) -> None:
    h = publishing
    life = life_source([claim("cancelled-lease")])
    h.plugin.life_engine = life
    runner = routine.RoutineRunner(h.plugin)
    h.rpc.llm_responses = ["是|适合分享", "公开正文"]
    h.rpc.image_results = [image_result()]

    async def scenario() -> None:
        entered = asyncio.Event()

        async def pending_request(args: Dict[str, Any]) -> Any:
            entered.set()
            await asyncio.Event().wait()

        if cancel_stage == "image":
            h.rpc.image_results = [pending_request]
        elif cancel_stage == "upload":
            h.transport.upload_results = [pending_request]
        else:
            h.transport.publish_results = [pending_request]
        task = asyncio.create_task(runner._try_post_feed(ActivityInfo(ActivityType.RELAXING, "午后休息")))
        try:
            await asyncio.wait_for(entered.wait(), timeout=2)
        finally:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

    asyncio.run(scenario())

    life.confirm.assert_not_awaited()
    h.retry_sleep.assert_not_awaited()
    if cancel_stage == "publish":
        assert runner.last_post_time == NOW
        assert life.quarantine.await_args.args == ("cancelled-lease",)
        life.release.assert_not_awaited()
        assert len(h.transport.publications) == 1
    else:
        assert runner.last_post_time == 0
        assert life.release.await_args.args == ("cancelled-lease",)
        life.quarantine.assert_not_awaited()
        assert not h.transport.publications


def test_routine_confirm_failure_quarantines_already_published_material(publishing: PublishHarness) -> None:
    h = publishing
    life = life_source([claim("unconfirmed-lease")], confirm=False)
    h.plugin.life_engine = life
    runner = routine.RoutineRunner(h.plugin)
    h.rpc.llm_responses = ["是|适合分享", "公开正文"]
    h.rpc.image_results = [image_result()]

    asyncio.run(runner._try_post_feed(ActivityInfo(ActivityType.RELAXING, "午后休息")))

    life.confirm.assert_awaited_once_with("unconfirmed-lease", receipt="offline-tid-1")
    life.quarantine.assert_awaited_once_with("unconfirmed-lease", error="空间已发布，但素材确认未成功")
    life.release.assert_not_awaited()
    assert runner.last_post_time == NOW
    assert len(h.transport.publications) == 1


def test_custom_material_is_a_topic_and_privacy_instruction_reaches_body_llm(publishing: PublishHarness) -> None:
    h = publishing
    instruction = "仅可公开虚构场景，不得提及任何私聊原文或联系人。"
    life = life_source([claim("custom-topic-lease", topic="custom", instruction=instruction)])
    h.plugin.life_engine = life
    h.plugin.config.send.custom_qqaccount = "111"
    h.rpc.llm_responses = ["是|适合分享", "公开虚构场景正文"]
    h.rpc.image_results = [image_result()]
    runner = routine.RoutineRunner(h.plugin)

    asyncio.run(runner._try_post_feed(ActivityInfo(ActivityType.RELAXING, "午后休息")))

    h.custom_content.assert_not_awaited()
    assert len(h.rpc.llm_calls) == 2
    actual_prompt = h.rpc.llm_calls[-1]["args"]["prompt"]
    assert "发布主题：custom" in actual_prompt
    assert "【本次素材的公开范围】" in actual_prompt
    assert instruction in actual_prompt
    assert h.transport.publications[0]["data"]["con"] == "公开虚构场景正文"
    assert "PRIVATE_SENTINEL" not in str(h.transport.calls)
    life.confirm.assert_awaited_once_with("custom-topic-lease", receipt="offline-tid-1")


def test_direct_custom_cannot_bypass_material_privacy_instruction(publishing: PublishHarness) -> None:
    h = publishing

    success, error = asyncio.run(feed_publish.send_feed(
        h.plugin, "custom", material_instruction="仅可公开虚构内容",
    ))

    assert not success
    assert "公开范围" in error
    h.custom_content.assert_not_awaited()
    assert not h.rpc.llm_calls
    assert not h.rpc.image_calls
    assert not h.transport.calls


@pytest.mark.parametrize(("failure_stage", "failure", "expected_stage", "expected_exception"), [
    ("upload", httpx.Response(503), "preparing", RuntimeError),
    ("upload", httpx.ReadTimeout("上传超时"), "preparing", httpx.ReadTimeout),
    ("publish", httpx.ConnectError("连接未建立"), "rejected", httpx.ConnectError),
    ("publish", httpx.ConnectTimeout("连接超时"), "rejected", httpx.ConnectTimeout),
    ("publish", httpx.PoolTimeout("连接池超时"), "rejected", httpx.PoolTimeout),
    ("publish", httpx.ReadTimeout("响应超时"), "uncertain", PublicationUncertainError),
    ("publish", httpx.WriteTimeout("写入超时"), "uncertain", PublicationUncertainError),
], ids=["upload-http", "upload-timeout", "connect-error", "connect-timeout", "pool-timeout", "read-timeout", "write-timeout"])
def test_qzone_transport_failures_preserve_publication_stage(
    publishing: PublishHarness, failure_stage: str, failure: Any,
    expected_stage: str, expected_exception: Type[BaseException],
) -> None:
    h = publishing
    if failure_stage == "upload":
        h.transport.upload_results = [failure]
    else:
        h.transport.publish_results = [failure]
    state = PublicationState()

    with pytest.raises(expected_exception):
        asyncio.run(h.qzone.publish_emotion("公开正文", [PNG_BYTES], publication=state))

    assert state.stage == expected_stage
    assert state.uncertain is (expected_stage == "uncertain")
    assert state.tid == ""
    assert len(h.transport.uploads) == 1
    assert len(h.transport.publications) == (0 if failure_stage == "upload" else 1)


@pytest.mark.parametrize(("response", "expected_stage", "expected_exception"), [
    (httpx.Response(500), "uncertain", PublicationUncertainError),
    (httpx.Response(200, text="not-json"), "uncertain", PublicationUncertainError),
    (httpx.Response(200, json={}), "uncertain", PublicationUncertainError),
    (httpx.Response(200, json={"code": 0}), "uncertain", PublicationUncertainError),
    (httpx.Response(200, json={"code": -1, "message": "明确拒绝"}), "rejected", RuntimeError),
], ids=["http-500", "invalid-json", "missing-code", "missing-tid", "rejected"])
def test_qzone_response_must_confirm_a_publication_id(
    publishing: PublishHarness, response: httpx.Response,
    expected_stage: str, expected_exception: Type[BaseException],
) -> None:
    h = publishing
    h.transport.publish_results = [response]
    state = PublicationState()

    with pytest.raises(expected_exception):
        asyncio.run(h.qzone.publish_emotion("公开正文", publication=state))

    assert state.stage == expected_stage
    assert state.uncertain is (expected_stage == "uncertain")
    assert state.tid == ""
    assert len(h.transport.publications) == 1


@pytest.mark.parametrize("cancel_stage", ["upload", "publish"])
def test_qzone_cancelled_request_exposes_its_publication_stage(
    publishing: PublishHarness, cancel_stage: str,
) -> None:
    h = publishing
    state = PublicationState()

    async def scenario() -> None:
        entered = asyncio.Event()

        async def pending_request(args: Dict[str, Any]) -> Any:
            entered.set()
            await asyncio.Event().wait()

        if cancel_stage == "upload":
            h.transport.upload_results = [pending_request]
        else:
            h.transport.publish_results = [pending_request]
        task = asyncio.create_task(h.qzone.publish_emotion("公开正文", [PNG_BYTES], publication=state))
        try:
            await asyncio.wait_for(entered.wait(), timeout=2)
            assert state.stage == ("preparing" if cancel_stage == "upload" else "submitting")
        finally:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

    asyncio.run(scenario())

    assert state.stage == ("preparing" if cancel_stage == "upload" else "uncertain")
    assert state.uncertain is (cancel_stage == "publish")
    assert state.tid == ""


@pytest.mark.parametrize("entry", ["generated", "direct"])
def test_confirmed_publication_is_recorded_once(publishing: PublishHarness, monkeypatch: pytest.MonkeyPatch, entry: str) -> None:
    h = publishing
    h.plugin.config.image.enable_image = False
    h.rpc.llm_responses = ["离线发布正文"]

    async def scenario() -> None:
        if entry == "generated":
            assert (await feed_publish.send_feed(h.plugin, "离线主题"))[0]
        elif entry == "direct":
            assert (await apis.send_feed_api(h.plugin, message="离线发布正文"))["result"]
        history = await h.plugin.persistence.recent_qzone_activity()
        assert len(history) == 1 and history[0]["kind"] == "publish"
        assert history[0]["fid"] == "offline-tid-1" and history[0]["text"] == "离线发布正文"

    asyncio.run(scenario())


def test_history_io_failure_does_not_retry_or_fail_confirmed_publication(
    publishing: PublishHarness, monkeypatch: pytest.MonkeyPatch,
) -> None:
    h = publishing

    def fail(entry: Dict[str, Any]) -> None:
        raise OSError("模拟磁盘已满")

    monkeypatch.setattr(h.plugin.persistence, "_record_qzone_activity", fail)
    result = asyncio.run(apis.send_feed_api(h.plugin, message="已确认正文"))
    assert result["result"] is True
    assert len(h.transport.publications) == 1


def test_send_feed_api_reports_uncertainty_without_retry(publishing: PublishHarness) -> None:
    h = publishing
    h.transport.publish_results = [httpx.ReadTimeout("发布响应超时")]

    result = asyncio.run(apis.send_feed_api(h.plugin, "调用方正文", [PNG_BASE64]))

    assert result["result"] is False
    assert result["uncertain"] is True
    assert asyncio.run(h.plugin.persistence.recent_qzone_activity()) == []
    assert len(h.transport.publications) == 1
    assert not h.rpc.image_calls


def test_zero_history_reference_skips_all_history_calls() -> None:
    config = MaiTracePluginConfig()
    config.send.history_number = 0
    plugin = SimpleNamespace(config=config)
    qzone = SimpleNamespace(get_send_history=AsyncMock(side_effect=AssertionError("历史查询应关闭")))
    prompt = asyncio.run(build_send_prompt(plugin, "午后", "自然的人格", "简短", qzone_api=qzone))
    qzone.get_send_history.assert_not_awaited()
    assert "午后" in prompt
