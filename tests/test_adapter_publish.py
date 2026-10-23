"""通过真实 SDK 验证两类适配器的发布选择、图片参数与不重复发帖。"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, Iterator, List
from unittest.mock import AsyncMock
import asyncio
import base64
import sys

from maibot_sdk.context import PluginContext
import httpx
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from MaiTrace.config import MaiTracePluginConfig
from MaiTrace.handlers import apis
from MaiTrace.services import feed_publish, qzone_publisher
from MaiTrace.services.adapter import discover_qq_adapters, resolve_bot_uin
from MaiTrace.services.cookie import fetch_cookies_by_adapter
from MaiTrace.services.persistence import PluginPersistence
from MaiTrace.services.qzone_api import PublicationState, QzoneAPI

PLUGIN_ID = "Rabbit-Jia-Er.MaiTrace"
ADAPTER_ID = "maibot-team.snowluma-adapter"
OFFICIAL_ID = "Mai-with-u.qq-adapter"
IMAGE_BYTES = b"offline-image-1"
IMAGE_BASE64 = base64.b64encode(IMAGE_BYTES).decode("ascii")


def api_entry(plugin_id: str, name: str, **changes: Any) -> Dict[str, Any]:
    return {"plugin_id": plugin_id, "name": name, "version": "1", "enabled": True, "public": True, **changes}


class AdapterRPC:
    """Host 外层与 OneBot 内层均保留，让 SDK 完成真正的跨插件解包。"""

    def __init__(self, *, app_name: str = "SnowLuma", prefixes: tuple[str, ...] = ("snowluma", "napcat")) -> None:
        names = ("system.get_login_info", "system.get_version_info", "account.get_cookies",
                 "qzone.send_qzone_msg", "qzone.get_qzone_msg_list")
        self.entries = [api_entry(OFFICIAL_ID, "adapter.avatar.get")]
        self.entries += [api_entry(ADAPTER_ID, f"adapter.{prefix}.{name}") for prefix in prefixes for name in names]
        self.calls: List[Dict[str, Any]] = []
        self.rpc_timeouts: List[int] = []
        self.app_name = app_name
        self.uin = "123456"
        self.login_uins: Dict[str, str] = {}
        self.app_names: Dict[str, str] = {}
        self.configured_uin = ""
        self.publish_result: Any = {"status": "ok", "retcode": 0, "data": {"tid": "adapter-tid"}}
        self.history = [{"tid": "old-tid", "content": "上次的散步", "time": 1_800_000_000, "images": []}]
        self.cookies: Dict[str, str] = {}
        self.login_error = False

    @property
    def api_calls(self) -> List[Dict[str, Any]]:
        return [call["args"] for call in self.calls if call["capability"] == "api.call"]

    @property
    def publications(self) -> List[Dict[str, Any]]:
        return [call for call in self.api_calls if call["api_name"].endswith(".qzone.send_qzone_msg")]

    async def __call__(
        self, method: str, plugin_id: str, payload: Dict[str, Any], **kwargs: Any,
    ) -> Any:
        assert method == "cap.call" and plugin_id == PLUGIN_ID
        self.calls.append(payload)
        capability, args = payload["capability"], payload["args"]
        if capability == "api.list":
            return {"success": True, "apis": self.entries}
        if capability == "config.get" and args["key"] == "bot.qq_account":
            return {"success": True, "value": self.configured_uin}
        assert capability == "api.call", f"未授权的离线 RPC: {capability}"
        assert args["version"] == "1"
        self.rpc_timeouts.append(kwargs["timeout_ms"])
        name = args["api_name"]
        assert any(name == f"{entry['plugin_id']}.{entry['name']}" for entry in self.entries)
        owner = name.split(".adapter.", 1)[0]
        if name.endswith(".system.get_login_info"):
            if self.login_error:
                raise RuntimeError("对端未连接")
            result = {"user_id": int(self.login_uins.get(owner, self.uin)), "nickname": "离线麦麦"}
        elif name.endswith(".system.get_version_info"):
            result = {"status": "ok", "retcode": 0, "data": {"app_name": self.app_names.get(owner, self.app_name)}}
        elif name.endswith(".account.get_cookies"):
            assert args["args"] == {"params": {"domain": "qzone.qq.com"}}
            result = {"status": "ok", "retcode": 0, "data": {
                "cookies": self.cookies.get(owner, "skey=offline;p_skey=offline-key;uin=o123456;p_uin=o123456"),
            }}
        elif name.endswith(".qzone.get_qzone_msg_list"):
            assert args["args"]["params"]["target_uin"] == int(self.uin)
            result = {"status": "ok", "retcode": 0, "data": {"msglist": self.history, "total": len(self.history)}}
        elif name.endswith(".qzone.send_qzone_msg"):
            result = self.publish_result
            if isinstance(result, BaseException):
                raise result
            if callable(result):
                result = await result()
        else:
            raise AssertionError(f"未授权的适配器 API: {name}")
        return {"success": True, "result": result}


@pytest.fixture(autouse=True)
def offline(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    attempts: List[str] = []

    async def reject_request(*args: Any, **kwargs: Any) -> None:
        attempts.append("HTTP")
        raise AssertionError("适配器发布回归禁止真实 HTTP")

    monkeypatch.setattr(httpx.AsyncClient, "request", reject_request)
    monkeypatch.setattr(httpx.AsyncClient, "send", reject_request)
    yield
    assert not attempts


def make_plugin(tmp_path: Path, rpc: AdapterRPC) -> SimpleNamespace:
    config = MaiTracePluginConfig()
    config.plugin.cookie_methods = ["adapter"]
    return SimpleNamespace(config=config, ctx=PluginContext(PLUGIN_ID, rpc_call=rpc),
                           persistence=PluginPersistence(tmp_path), schedule_engine=None, life_engine=None)


@pytest.mark.parametrize("prefixes", [("snowluma",), ("napcat",), ("snowluma", "napcat")])
@pytest.mark.parametrize("image_count", [0, 2])
def test_snowluma_direct_api_uses_registered_alias_and_preserves_images(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, prefixes: tuple[str, ...], image_count: int,
) -> None:
    rpc = AdapterRPC(prefixes=prefixes)
    plugin = make_plugin(tmp_path, rpc)
    refresh = AsyncMock(side_effect=AssertionError("SnowLuma 直发不需要本地 Cookie"))
    monkeypatch.setattr(qzone_publisher, "renew_cookies", refresh)
    supplied = [IMAGE_BASE64] * image_count

    result = asyncio.run(apis.send_feed_api(plugin, "准备好的正文", supplied))

    assert result["result"] and "adapter-tid" in result["message"]
    assert len(rpc.publications) == 1
    expected_prefix = "snowluma" if "snowluma" in prefixes else "napcat"
    assert rpc.publications[0]["api_name"] == f"{ADAPTER_ID}.adapter.{expected_prefix}.qzone.send_qzone_msg"
    expected = {"content": "准备好的正文", "ugc_right": 1}
    if supplied:
        expected["images"] = ["base64://" + image for image in supplied]
    assert rpc.publications[0]["args"] == {"params": expected}
    assert rpc.rpc_timeouts == [65000, 65000, 65000]
    refresh.assert_not_awaited()
    history = asyncio.run(plugin.persistence.recent_qzone_activity())
    assert len(history) == 1 and history[0]["fid"] == "adapter-tid"


def test_generated_feed_uses_native_history_and_same_publisher(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    rpc = AdapterRPC()
    plugin = make_plugin(tmp_path, rpc)
    persona = SimpleNamespace(personality="记录日常", style="自然", self_description="", reference_image_path="")
    monkeypatch.setattr(feed_publish, "resolve_persona", AsyncMock(return_value=persona))

    async def prompt(plugin: Any, topic: str, personality: str, style: str, **kwargs: Any) -> str:
        assert kwargs["qzone_api"].uin == rpc.uin
        assert "上次的散步" in await kwargs["qzone_api"].get_send_history(5)
        return "离线主题提示词"

    monkeypatch.setattr(feed_publish, "build_send_prompt", prompt)
    generate = AsyncMock(return_value=(True, "新主题正文"))
    monkeypatch.setattr(feed_publish, "LLMRunner", lambda *args, **kwargs: SimpleNamespace(generate=generate))
    monkeypatch.setattr(feed_publish, "collect_images_for_feed", AsyncMock(return_value=[IMAGE_BYTES]))
    refresh = AsyncMock()
    monkeypatch.setattr(qzone_publisher, "renew_cookies", refresh)
    state = PublicationState()

    result = asyncio.run(feed_publish.send_feed(plugin, "散步", publication=state))

    assert result == (True, "新主题正文")
    assert state.stage == "confirmed" and state.tid == "adapter-tid"
    assert len(rpc.publications) == 1
    refresh.assert_not_awaited()


def test_napcat_profile_uses_cookie_http_even_with_snowluma_alias(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    rpc = AdapterRPC(app_name="NapCat")
    plugin = make_plugin(tmp_path, rpc)
    qzone = QzoneAPI({"p_skey": "offline-key"}, rpc.uin)
    publish = AsyncMock(return_value="http-tid")
    monkeypatch.setattr(qzone, "publish_emotion", publish)
    monkeypatch.setattr(qzone_publisher, "create_qzone_api", lambda uin, **kwargs: qzone)

    result = asyncio.run(apis.send_feed_api(plugin, "NapCat 正文", [IMAGE_BASE64]))

    assert result["result"] and "http-tid" in result["message"]
    assert not rpc.publications
    publish.assert_awaited_once()
    assert publish.await_args.args == ("NapCat 正文", [IMAGE_BYTES])
    cookie_file = plugin.persistence.get_cookie_file_path(rpc.uin)
    assert cookie_file.exists() and "p_skey" in cookie_file.read_text(encoding="utf-8")
    assert len([call for call in rpc.api_calls if call["api_name"].endswith(".account.get_cookies")]) == 1


@pytest.mark.parametrize("response", [
    RuntimeError("适配器断连"), TimeoutError("RPC 超时"), None,
    {"status": "ok", "retcode": 0, "data": {}},
    {"status": "async", "retcode": 1, "data": {"tid": "pending"}},
    {"status": "failed", "retcode": 100, "message": "对端失败"},
    {"status": "ok", "retcode": 0, "data": {"tid": True}},
    {"status": "ok", "retcode": 0, "data": {"tid": "0"}},
    {"status": "ok", "retcode": 0, "data": {"tid": -1}},
    {"success": False, "error": "Host 超时"},
])
def test_unconfirmed_native_publication_never_switches_to_cookie_or_retries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, response: Any,
) -> None:
    rpc = AdapterRPC()
    rpc.publish_result = response
    plugin = make_plugin(tmp_path, rpc)
    refresh = AsyncMock()
    monkeypatch.setattr(qzone_publisher, "renew_cookies", refresh)

    result = asyncio.run(apis.send_feed_api(plugin, "待核对正文", [IMAGE_BASE64]))

    assert result["result"] is False and result["uncertain"] is True
    assert len(rpc.publications) == 1
    refresh.assert_not_awaited()
    assert asyncio.run(plugin.persistence.recent_qzone_activity()) == []


def test_cancelling_native_publish_preserves_uncertain_state(tmp_path: Path) -> None:
    rpc = AdapterRPC()
    plugin = make_plugin(tmp_path, rpc)
    state = PublicationState()

    async def scenario() -> None:
        entered = asyncio.Event()

        async def pending() -> None:
            entered.set()
            await asyncio.Event().wait()

        rpc.publish_result = pending
        publisher = await qzone_publisher.create_qzone_publisher(plugin)
        assert publisher is not None
        task = asyncio.create_task(publisher.publish_emotion("取消时的正文", publication=state))
        try:
            await asyncio.wait_for(entered.wait(), timeout=2)
            assert state.stage == "submitting"
        finally:
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

    asyncio.run(scenario())
    assert state.uncertain and state.stage == "uncertain" and len(rpc.publications) == 1


def test_official_adapter_without_real_qq_reports_missing_space_account(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    rpc = AdapterRPC()
    rpc.entries = [api_entry(OFFICIAL_ID, "adapter.avatar.get")]
    rpc.configured_uin = "Official_OpenID"
    plugin = make_plugin(tmp_path, rpc)
    refresh = AsyncMock()
    monkeypatch.setattr(qzone_publisher, "renew_cookies", refresh)

    result = asyncio.run(apis.send_feed_api(plugin, "官方机器人正文"))

    assert result["result"] is False and result["uncertain"] is False
    assert "真实 QQ 账号" in result["message"]
    assert not rpc.api_calls
    refresh.assert_not_awaited()


def test_login_account_precedes_host_fallback_and_disconnected_adapter_uses_fallback(tmp_path: Path) -> None:
    rpc = AdapterRPC()
    rpc.configured_uin = "999999"
    plugin = make_plugin(tmp_path, rpc)
    assert asyncio.run(resolve_bot_uin(plugin.ctx)) == "123456"
    rpc.login_error = True
    assert asyncio.run(resolve_bot_uin(plugin.ctx)) == "999999"


def test_cookie_priority_overrides_cached_adapter_and_rejects_wrong_account(tmp_path: Path) -> None:
    rpc = AdapterRPC()
    preferred = "custom.preferred"
    rpc.entries += [api_entry(preferred, "adapter.napcat.account.get_cookies")]
    rpc.cookies[preferred] = "p_skey=preferred-key;uin=o123456"
    plugin = make_plugin(tmp_path, rpc)
    plugin.persistence.cookie_state["adapter_id"] = ADAPTER_ID

    cookies = asyncio.run(fetch_cookies_by_adapter(plugin.ctx, ["preferred"], storage=plugin.persistence, uin=rpc.uin))

    assert cookies and cookies["p_skey"] == "preferred-key"
    assert plugin.persistence.cookie_state["adapter_id"] == preferred
    assert len(rpc.api_calls) == 1 and rpc.api_calls[0]["api_name"].startswith(preferred + ".")
    rpc.calls.clear()
    rpc.cookies[preferred] = "p_skey=other-account;uin=o999999"
    cookies = asyncio.run(fetch_cookies_by_adapter(plugin.ctx, ["preferred"], storage=plugin.persistence, uin=rpc.uin))
    assert cookies and cookies["p_skey"] == "offline-key"
    assert plugin.persistence.cookie_state["adapter_id"] == ADAPTER_ID


def test_discovery_ignores_official_disabled_and_other_api_versions(tmp_path: Path) -> None:
    rpc = AdapterRPC()
    rpc.entries += [api_entry("custom.disabled", "adapter.snowluma.qzone.send_qzone_msg", enabled=False),
                    api_entry("custom.v2", "adapter.napcat.account.get_cookies", version="2")]
    plugin = make_plugin(tmp_path, rpc)

    adapters = asyncio.run(discover_qq_adapters(plugin.ctx))

    assert len(adapters) == 1 and adapters[0].plugin_id == ADAPTER_ID
    assert adapters[0].api_names["account.get_cookies"] == f"{ADAPTER_ID}.adapter.snowluma.account.get_cookies"


def test_native_rpc_honors_component_budget_without_forwarding_timeout(tmp_path: Path) -> None:
    rpc = AdapterRPC()
    for entry in rpc.entries:
        if entry["name"].endswith(".qzone.send_qzone_msg"):
            entry["metadata"] = {"timeout_ms": 180000}
    plugin = make_plugin(tmp_path, rpc)

    assert asyncio.run(apis.send_feed_api(plugin, "多图正文", [IMAGE_BASE64]))["result"]

    assert rpc.rpc_timeouts[-1] == 185000
    assert rpc.publications[0]["args"] == {"params": {
        "content": "多图正文", "ugc_right": 1, "images": ["base64://" + IMAGE_BASE64],
    }}


def test_explicit_napcat_priority_keeps_same_account_for_publication(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    rpc = AdapterRPC()
    napcat_id = "custom.napcat-adapter"
    rpc.entries += [api_entry(napcat_id, f"adapter.napcat.{name}") for name in (
        "system.get_login_info", "system.get_version_info", "account.get_cookies", "qzone.send_qzone_msg",
    )]
    rpc.login_uins[napcat_id] = "777777"
    rpc.app_names[napcat_id] = "NapCat"
    rpc.cookies[napcat_id] = "p_skey=napcat-key;uin=o777777"
    plugin = make_plugin(tmp_path, rpc)
    plugin.config.plugin.adapter_priority = [napcat_id]
    qzone = QzoneAPI({"p_skey": "napcat-key"}, "777777")
    qzone.publish_emotion = AsyncMock(return_value="napcat-tid")

    def client(uin: str, **kwargs: Any) -> QzoneAPI:
        assert uin == "777777"
        return qzone

    monkeypatch.setattr(qzone_publisher, "create_qzone_api", client)

    result = asyncio.run(apis.send_feed_api(plugin, "指定账号的正文"))

    assert result["result"] and "napcat-tid" in result["message"]
    assert not rpc.publications
    assert all(call["api_name"].startswith(napcat_id + ".") for call in rpc.api_calls)
