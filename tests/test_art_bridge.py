"""在安装绘卷的工作区验证真实 SDK → 绘卷 API → 自拍流水线，仅替换生图端。"""

from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List
from unittest.mock import AsyncMock
import asyncio
import base64
import json
import sys
import urllib.request

from maibot_sdk.context import PluginContext
import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

pytest.importorskip("mais_art_journal", reason="此集成回归需要同工作区安装绘卷")

import requests

from mais_art_journal.core import pipeline as art_pipeline
from mais_art_journal.plugin import MaisArtPlugin

from MaiTrace.config import MaiTracePluginConfig
from MaiTrace.services.feed_image import collect_images_for_feed

IMAGE = base64.b64encode(bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4"
    "890000000d49444154789c63000100000005000100ee63d28d0000000049454e44ae426082"
)).decode("ascii")
REFERENCE = base64.b64encode(b"offline-character-reference").decode("ascii")


@pytest.mark.parametrize(("api_format", "syntax", "expected_strength"), [
    ("gemini", "natural", None), ("openai", "tags", 0.6),
])
@pytest.mark.parametrize("optimize", [False, True])
def test_reference_outfit_is_replaced_in_the_actual_image_request(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, api_format: str, syntax: str,
    expected_strength: float | None, optimize: bool,
) -> None:
    def reject_network(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("绘卷桥接回归禁止真实网络请求")

    monkeypatch.setattr(requests.Session, "request", reject_network)
    monkeypatch.setattr(httpx.AsyncClient, "send", reject_network)
    monkeypatch.setattr(urllib.request.OpenerDirector, "open", reject_network)
    art = MaisArtPlugin()
    config_data = art.build_default_config()
    config_data.update({
        "basic": {"prompt_optimizer_enabled": optimize},
        "selfie": {"enabled": True, "prompt_prefix": "silver hair, blue eyes, school uniform"},
        "models": {"items": [{"id": "offline", "format": api_format, "model": "offline-model",
                              "base_url": "https://offline.invalid", "api_key": "offline-key",
                              "support_img2img": True, "prompt_syntax": syntax,
                              "selfie_reference_strength": 0.6,
                              "fixed_size_enabled": True, "default_size": "1024x1024"}]},
    })
    art.set_plugin_config(config_data)
    art._plugin_dir = str(tmp_path)
    scene_llm = AsyncMock(return_value={"success": True, "response": json.dumps({
        "action": "walking with relaxed hands", "environment": "park path at night",
        "expression": "calm expression", "lighting": "soft evening light", "outfit": "red tracksuit",
        "selfie_style": "photo", "character_identity": "silver hair, blue eyes", "visual_style": "",
    })})
    art._set_context(SimpleNamespace(llm=SimpleNamespace(generate=scene_llm)))
    backend = SimpleNamespace(generate_image=AsyncMock(return_value=(True, IMAGE)))
    real_context = art_pipeline.make_pipeline_context
    contexts: List[Any] = []

    def context(plugin: Any, request: Any) -> Any:
        result = real_context(plugin, request)
        result.client_factory = lambda api_format: backend
        contexts.append(request)
        return result

    monkeypatch.setattr(art_pipeline, "make_pipeline_context", context)
    calls: List[Dict[str, Any]] = []

    async def rpc(method: str, plugin_id: str, payload: Dict[str, Any], **kwargs: Any) -> Any:
        assert method == "cap.call" and payload["capability"] == "api.call"
        args = payload["args"]
        assert args["api_name"] == "1021143806.mais_art_journal.generate_image"
        assert args["version"] == "1" and kwargs["timeout_ms"] == 185000
        calls.append(args["args"])
        result = await art.api_generate_image(**args["args"])
        assert result["success"], result["error"]
        assert result["outfit"] == "red tracksuit"
        return {"success": True, "result": result}

    config = MaiTracePluginConfig()
    config.image.enable_image = True
    config.image.image_mode = "only_ai"
    config.image.ai_content_mode = "selfie"
    config.image.pic_plugin_model = "offline"
    config.image.image_retry = 0
    plugin = SimpleNamespace(config=config, ctx=PluginContext("Rabbit-Jia-Er.MaiTrace", rpc_call=rpc))
    reference = tmp_path / "reference.png"
    reference.write_bytes(base64.b64decode(REFERENCE))
    metadata = {"activity_location": "公园", "activity_time_window": "18:00-19:00",
                "activity_as_of": "2026-10-23T18:30:00+08:00", "activity_timezone": "Asia/Shanghai",
                "activity_scene": "傍晚沿树下散步"}

    images = asyncio.run(collect_images_for_feed(
        plugin, "散步时的照片", reference_image_path=str(reference), activity_type="exercising",
        activity_description="晚饭后散步", outfit="red tracksuit", activity_context=metadata,
    ))

    assert images == [base64.b64decode(IMAGE)]
    assert len(calls) == 1 and calls[0]["input_image_base64"] == REFERENCE
    assert all(calls[0][key] == value for key, value in metadata.items())
    assert "strength" not in calls[0] and calls[0]["use_cache"] is False
    backend.generate_image.assert_awaited_once()
    request = backend.generate_image.await_args.kwargs
    assert request["input_image_base64"] == REFERENCE
    assert request["strength"] == expected_strength
    assert "silver hair" in request["prompt"] and "blue eyes" in request["prompt"]
    assert "red tracksuit" in request["prompt"] and "school uniform" not in request["prompt"]
    assert contexts[0].activity_location == "公园" and contexts[0].schedule_recall is False
    if syntax == "natural":
        assert "replacing the reference clothing" in request["prompt"]
    if optimize:
        scene_llm.assert_awaited_once()
        scene_data = json.loads(scene_llm.await_args.kwargs["prompt"][1]["content"])
        assert scene_data["outfit"] == "red tracksuit" and scene_data["location"] == "公园"
        assert scene_data["timezone"] == "Asia/Shanghai"
    else:
        scene_llm.assert_not_awaited()
