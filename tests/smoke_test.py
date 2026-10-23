"""MaiTrace 的离线 smoke 检查：当前 SDK 组件、精简配置、配图、Routine 和命令权限。

不启动 MaiBot，不访问 QQ 空间；外部能力全部替换为内存结果。
在 MaiBot 根目录运行 .venv/Scripts/python.exe plugins/MaiTrace/tests/smoke_test.py。
成功退出码 0，失败 1。
"""

from __future__ import annotations

import asyncio
import ast
import base64
import datetime
import os
import sys
import time
import traceback
import warnings
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from typing import Any, Dict, List, Tuple


# ---------- 路径 / 环境 ----------

_PLUGIN_DIR = Path(__file__).resolve().parent.parent
_PLUGINS_DIR = _PLUGIN_DIR.parent
_PROJECT_DIR = _PLUGINS_DIR.parent

# 让 import MaiTrace.* 能工作
sys.path.insert(0, str(_PLUGINS_DIR))

# 任何 DeprecationWarning 都直接报错（确保 @Action 之类没残留）
warnings.simplefilter("error", DeprecationWarning)


# ---------- 极简测试框架 ----------

_passed: List[str] = []
_failed: List[Tuple[str, str]] = []


def _run(name: str, fn):
    try:
        result = fn()
        if asyncio.iscoroutine(result):
            asyncio.run(result)
        _passed.append(name)
        print(f"  [PASS] {name}")
    except Exception as exc:
        _failed.append((name, f"{type(exc).__name__}: {exc}"))
        print(f"  [FAIL] {name}: {type(exc).__name__}: {exc}")
        traceback.print_exc(file=sys.stdout)


# ---------- Fake PNG / Mock Ctx ----------

FAKE_PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4"
    "890000000d49444154789c63000100000005000100ee63d28d0000000049454e44ae426082"
)
FAKE_B64 = base64.b64encode(FAKE_PNG).decode()


class MockConfigCap:
    """模拟 ctx.config —— 提供 personality / bot / 跨插件配置。"""

    def __init__(
        self,
        *,
        personality: str = "你是银发狐妖，慵懒优雅。",
        reply_style: str = "默认风格",
        multiple_styles: List[str] | None = None,
        multiple_probability: float = 0.5,
        nickname: str = "麦麦",
        alias: List[str] | None = None,
        art_prompt_prefix: str = "silver hair, red eyes, 1girl",
        art_selfie_enabled: bool = True,
        art_reference_path: str = "",
        qq_account: str = "10001",
    ):
        self._data = {
            "personality.personality": personality,
            "personality.reply_style": reply_style,
            "personality.multiple_reply_style": multiple_styles or ["撩拨", "克制", "宠溺"],
            "personality.multiple_probability": multiple_probability,
            "bot.nickname": nickname,
            "bot.alias_names": alias or ["小麦"],
            "bot.qq_account": qq_account,
        }
        self._art_prompt_prefix = art_prompt_prefix
        self._art_selfie_enabled = art_selfie_enabled
        self._art_reference_path = art_reference_path  # 测试时传绝对路径

    async def get(self, key, default=None):
        return self._data.get(key, default)

    async def get_plugin(self, plugin_id):
        if plugin_id == "1021143806.mais_art_journal":
            return {
                "selfie": {
                    "enabled": self._art_selfie_enabled,
                    "prompt_prefix": self._art_prompt_prefix,
                    "reference_image_path": self._art_reference_path,
                }
            }
        return {}


class MockAPICap:
    """模拟 ctx.api —— 记录所有 call。"""

    def __init__(self, *, image_b64: str = FAKE_B64, planning_payload: Dict | None = None):
        self.calls: List[Dict[str, Any]] = []
        self._image_b64 = image_b64
        self._planning_payload = planning_payload
        self._planning_error: Exception | None = None
        # v4.0：adapter 取 cookie 支持 napcat / snowluma 双探测。
        # _adapter_ids 决定 api.list() 报告哪些插件提供 get_cookies；
        # _adapter_ok 决定哪些插件真的能返回 cookie（用来模拟"装了但拿不到"）。
        self._adapter_ids: List[str] = []
        self._adapter_ok: set[str] = set()

    def set_adapters(self, ids: List[str], ok: List[str] | None = None):
        """配置模拟的适配器环境。

        Args:
            ids: api.list() 中报告提供 get_cookies 的 plugin_id 列表。
            ok: 其中真正能返回 cookie 的；省略时全部可用。
        """
        self._adapter_ids = list(ids)
        self._adapter_ok = set(ids if ok is None else ok)

    def set_planning_error(self, exc: Exception):
        self._planning_error = exc

    def set_planning_payload(self, payload: Dict):
        self._planning_payload = payload
        self._planning_error = None

    async def list(self, *, plugin_id: str = ""):
        del plugin_id
        return [
            {
                "name": "adapter.napcat.account.get_cookies",
                "full_name": f"{pid}.adapter.napcat.account.get_cookies",
                "plugin_id": pid,
                "version": "1",
                "public": True,
                "enabled": True,
            }
            for pid in self._adapter_ids
        ]

    async def call(self, api_name: str, **kwargs):
        self.calls.append({"name": api_name, "kwargs": kwargs})
        if "generate_image" in api_name:
            return {
                "success": True,
                "image_base64": self._image_b64,
                "model_id": kwargs.get("model_id", ""),
                "size": "1024x1024",
                "is_img2img": False,
                "error": "",
            }
        if "get_current_activity" in api_name:
            if self._planning_error is not None:
                raise self._planning_error
            return self._planning_payload or {"has_activity": False}
        if api_name.endswith(".adapter.napcat.account.get_cookies"):
            owner = api_name[: -len(".adapter.napcat.account.get_cookies")]
            if owner in self._adapter_ok:
                return {"status": "ok", "data": {"cookies": "p_skey=fake; uin=o123456"}}
            return {"status": "failed", "data": {}}
        return None


class MockEmojiCap:
    async def get_by_description(self, description=""):
        return {"base64": FAKE_B64}


class MockLLMCap:
    """模拟 ctx.llm —— 默认返回失败响应（让 LLMRunner 走错误分支不报警）。

    用 ``set_response(success=..., text=...)`` 改返回值；用
    ``set_exception(exc)`` 让下次 generate 抛指定异常。
    """

    def __init__(self):
        self.calls: list[dict] = []
        self._response = {"success": False, "response": "", "error": "no llm in smoke"}
        self._exc: Exception | None = None

    def set_response(self, *, success: bool = True, text: str = ""):
        self._response = {"success": success, "response": text}
        self._exc = None

    def set_exception(self, exc: Exception):
        self._exc = exc

    async def generate(self, prompt, model="", temperature=0.7, max_tokens=2000, *, task_name="", **kwargs):
        self.calls.append({"prompt": prompt, "task_name": task_name, "temperature": temperature, **kwargs})
        if self._exc is not None:
            raise self._exc
        return self._response


class MockSendCap:
    def __init__(self):
        self.sent = []

    async def text(self, text, stream_id, **_):
        self.sent.append({"type": "text", "text": text, "stream_id": stream_id})
        return True


class MockCtx:
    def __init__(self, **kwargs):
        self.config = MockConfigCap(**kwargs)
        self.api = MockAPICap()
        self.emoji = MockEmojiCap()
        self.llm = MockLLMCap()
        self.send = MockSendCap()
        self.capability_calls: list[dict] = []

    async def call_capability(self, capability: str, timeout_ms: int | None = None, **kwargs):
        """模拟 SDK 的底层能力入口。

        模型代理与跨插件 API 最终都通过这个入口调用 Host；
        生图需要分别设置目标 API 参数和 RPC 超时。
        """
        self.capability_calls.append({
            "capability": capability,
            "timeout_ms": timeout_ms,
            "args": dict(kwargs),
        })
        if capability == "llm.generate":
            return await self.llm.generate(
                kwargs.get("prompt", ""),
                task_name=kwargs.get("task_name", ""),
                temperature=kwargs.get("temperature", 0.7),
                max_tokens=kwargs.get("max_tokens", 2000),
            )
        if capability == "api.call":
            return await self.api.call(kwargs.get("api_name", ""), **(kwargs.get("args") or {}))
        if capability == "api.list":
            return await self.api.list()
        raise AssertionError(f"MockCtx 未实现的 capability: {capability}")


def _make_plugin(**ctx_kwargs):
    """创建插件实例，注入默认配置，挂上 MockCtx。"""
    import MaiTrace.plugin as P  # noqa
    p = P.create_plugin()
    p.set_plugin_config(p.build_default_config())
    p._ctx = MockCtx(**ctx_kwargs)
    p._test_data_directory = TemporaryDirectory(prefix="maitrace-smoke-")
    p._ctx.paths = SimpleNamespace(data_dir=Path(p._test_data_directory.name))
    return p


# ============================================================
# 测试 1. 全部改动文件 ast.parse
# ============================================================


def test_syntax():
    files = [
        "_manifest.json",  # 用 json.load 验证
        "plugin.py",
        "config/__init__.py",
        "handlers/actions.py",
        "handlers/apis.py",
        "handlers/commands.py",
        "services/cookie.py",
        "services/feed_image.py",
        "services/feed_publish.py",
        "services/feed_read.py",
        "services/llm_runner.py",
        "services/monitor.py",
        "services/persona.py",
        "services/prompts.py",
        "services/routine.py",
        "utils/__init__.py",
        "utils/untrusted.py",
    ]
    files.extend(str(path.relative_to(_PLUGIN_DIR)) for folder in ("config", "services", "handlers", "utils")
                 for path in (_PLUGIN_DIR / folder).rglob("*.py"))
    import json
    for rel in files:
        full = _PLUGIN_DIR / rel
        if not full.exists():
            raise FileNotFoundError(rel)
        text = full.read_text(encoding="utf-8")
        if rel.endswith(".json"):
            json.loads(text)
        else:
            ast.parse(text, str(full))


# ============================================================
# 测试 2. 插件实例化无 DeprecationWarning
# ============================================================


def test_create_plugin_clean():
    import MaiTrace.plugin as P
    inst = P.create_plugin()
    # 无异常即 PASS（DeprecationWarning 已设为 error）
    return inst


# ============================================================
# 测试 3. 当前组件注册和退役组件检查
# ============================================================


def test_components():
    import MaiTrace.plugin as P
    inst = P.create_plugin()
    inst.set_plugin_config(inst.build_default_config())
    components = inst.get_components()
    names_and_types = {(c["name"], c["type"]) for c in components}
    expected = {
        ("zn", "COMMAND"),
        ("send_feed", "TOOL"),
        ("read_feed", "TOOL"),
        ("recall_qzone_activity", "TOOL"),
        ("send_feed_api", "API"),
        ("get_feeds_list_api", "API"),
        ("publish_topic_api", "API"),
        ("get_planning_status_v6", "TOOL"),
        ("generate_schedule_v6", "TOOL"),
        ("update_schedule_v6", "TOOL"),
        ("get_current_activity", "API"),
        ("get_life_state", "API"),
        ("get_current_scene", "API"),
        ("get_environment_snapshot", "API"),
        ("create_proactive_opportunity", "API"),
    }
    missing = expected - names_and_types
    if missing:
        raise AssertionError(f"缺少组件: {missing}")
    names = {entry["name"] for entry in components}
    retired = {"manage_goal_v6", "apply_schedule_v6", "get_user_relationship", "web_search", "browse_web", "emotion_analysis"}
    assert names.isdisjoint(retired)
    assert {"maitrace_planner_life", "maitrace_replyer_life", "maitrace_life"} <= names
    assert not names.intersection({"maitrace_receive", "maitrace_before_send", "maitrace_after_send"})
    inst.config.messaging.enabled = True
    enabled_names = {entry["name"] for entry in inst.get_components()}
    assert {"maitrace_receive", "maitrace_before_send", "maitrace_after_send"} <= enabled_names
    # API 必须 public=True
    for c in components:
        if c["type"] == "API":
            assert c["metadata"].get("public") is True, f"{c['name']} 应该 public=True"


# ============================================================
# 测试 4. 当前配置段齐全
# ============================================================


def test_config_sections():
    import MaiTrace.plugin as P
    inst = P.create_plugin()
    cfg = inst.build_default_config()
    expected_sections = {
        "plugin", "send", "image", "read", "monitor", "routine", "llm", "persona",
        "schedule", "inject", "goal_cleanup", "life", "environment", "messaging",
    }
    assert set(cfg) == expected_sections
    assert {"self_description", "use_multiple_reply_style"} <= cfg["persona"].keys()
    assert "adapter_priority" in cfg["plugin"]
    assert cfg["life"]["diary_enabled"] is True
    assert not any(key.startswith("scheduled_") for key in cfg["routine"])
    assert not {"private_debounce", "group_debounce", "text_wait_seconds"} & cfg["messaging"].keys()


# ============================================================
# 测试 4b. 旧配置升级
# ============================================================


def test_migrate_legacy_to_current():
    import MaiTrace.plugin as P
    inst = P.create_plugin()
    old = {
        "plugin": {"config_version": "3.1.0", "enabled": True},
        "send": {"enable_image": False},
        "diary": {"enabled": True},
        "diary_model": {"api_key": "sk-should-be-dropped", "api_timeout": 420},
        "information": {"enabled": True},
    }
    normalized, changed = inst.normalize_plugin_config(old)
    assert changed
    assert normalized["plugin"]["config_version"] == inst.build_default_config()["plugin"]["config_version"]
    assert normalized["image"]["enable_image"] is False
    assert {"diary", "diary_model", "information"}.isdisjoint(normalized)
    assert "sk-should-be-dropped" not in repr(normalized)


# ============================================================
# 测试 4c. adapter 探测：napcat / snowluma 同一份代码通吃
# ============================================================


def _reset_cookie_state(plugin):
    """重置当前实例状态，并拦掉统计落盘。

    ``fetch_cookies_by_adapter`` 会调 ``record_cookie_attempt`` 写
    ``data/cookie_stats.json``；冒烟测试不应该污染真实数据目录，所以在这里把它
    换成内存收集器（返回值供用例断言"按 adapter 分别记成功率"）。
    """
    plugin.persistence.cookie_state.update({
        "last_save_time": 0.0, "last_method": "", "last_error": "",
        "uin": "", "adapter_id": "",
    })
    recorded: List[Tuple[str, bool]] = []

    async def _fake_record(method: str, success: bool) -> None:
        recorded.append((method, success))

    plugin.persistence.record_cookie_attempt = _fake_record
    return recorded


def test_adapter_discovery_napcat_only():
    """只装 napcat-adapter：应命中它。"""
    from MaiTrace.services.cookie import fetch_cookies_by_adapter
    p = _make_plugin()
    _reset_cookie_state(p)
    p.ctx.api.set_adapters(["maibot-team.napcat-adapter"])
    got = asyncio.run(fetch_cookies_by_adapter(p.ctx, storage=p.persistence))
    assert got and got.get("p_skey") == "fake", f"napcat 环境应取到 cookie，实际 {got}"
    from MaiTrace.services import cookie as C
    assert p.persistence.cookie_state["adapter_id"] == "maibot-team.napcat-adapter"


def test_adapter_discovery_snowluma_only():
    """只装 snowluma-adapter：**不改任何配置**也应该命中（P0 的核心验收条件）。"""
    from MaiTrace.services.cookie import fetch_cookies_by_adapter
    p = _make_plugin()
    _reset_cookie_state(p)
    p.ctx.api.set_adapters(["maibot-team.snowluma-adapter"])
    got = asyncio.run(fetch_cookies_by_adapter(p.ctx, storage=p.persistence))
    assert got and got.get("p_skey") == "fake", f"snowluma 环境应取到 cookie，实际 {got}"
    from MaiTrace.services import cookie as C
    assert p.persistence.cookie_state["adapter_id"] == "maibot-team.snowluma-adapter", (
        f"应命中 snowluma，实际 {p.persistence.cookie_state['adapter_id']}"
    )
    # 探测必须走 api.list，不能靠写死 plugin_id
    assert any(
        c["name"].startswith("maibot-team.snowluma-adapter.")
        for c in p.ctx.api.calls
    ), "应向 snowluma-adapter 发起调用"


def test_adapter_priority_respected():
    """两个都装时，adapter_priority 决定先试哪个（支持只写后缀）。"""
    from MaiTrace.services.cookie import fetch_cookies_by_adapter
    p = _make_plugin()
    _reset_cookie_state(p)
    both = ["maibot-team.napcat-adapter", "maibot-team.snowluma-adapter"]
    p.ctx.api.set_adapters(both)
    got = asyncio.run(fetch_cookies_by_adapter(p.ctx, ["snowluma-adapter"], storage=p.persistence))
    assert got, "应取到 cookie"
    first_call = next(
        c["name"] for c in p.ctx.api.calls
        if c["name"].endswith(".adapter.napcat.account.get_cookies")
    )
    assert first_call.startswith("maibot-team.snowluma-adapter."), (
        f"偏好 snowluma 时应先试它，实际先试了 {first_call}"
    )


def test_adapter_falls_through_to_next():
    """装了两个但第一个取不到时，应继续试第二个而不是直接失败。"""
    from MaiTrace.services.cookie import fetch_cookies_by_adapter
    p = _make_plugin()
    recorded = _reset_cookie_state(p)
    p.ctx.api.set_adapters(
        ["maibot-team.napcat-adapter", "maibot-team.snowluma-adapter"],
        ok=["maibot-team.snowluma-adapter"],
    )
    got = asyncio.run(fetch_cookies_by_adapter(p.ctx, storage=p.persistence))
    assert got, "第一个失败应继续试第二个"
    from MaiTrace.services import cookie as C
    assert p.persistence.cookie_state["adapter_id"] == "maibot-team.snowluma-adapter"
    # 成功率按 adapter 分别记：napcat 记失败、snowluma 记成功
    assert ("adapter:maibot-team.napcat-adapter", False) in recorded, f"实际记录 {recorded}"
    assert ("adapter:maibot-team.snowluma-adapter", True) in recorded, f"实际记录 {recorded}"


def test_adapter_none_installed():
    """一个 adapter 都没装：应返回 None 而不是抛异常。"""
    from MaiTrace.services.cookie import fetch_cookies_by_adapter
    p = _make_plugin()
    _reset_cookie_state(p)
    p.ctx.api.set_adapters([])
    got = asyncio.run(fetch_cookies_by_adapter(p.ctx, storage=p.persistence))
    assert got is None, f"无 adapter 时应返回 None，实际 {got}"


# ============================================================
# 测试 5. _manifest.json 关键字段
# ============================================================

def test_manifest():
    import json
    manifest = json.loads((_PLUGIN_DIR / "_manifest.json").read_text(encoding="utf-8"))
    assert manifest["version"] == "6.2.4", f"version 应 6.2.4，实际 {manifest['version']}"
    assert manifest["host_application"]["max_version"] != "1.0.0", "host max 不能写死 1.0.0"
    caps = set(manifest["capabilities"])
    for cap in ("chat.get_stream_by_user_id", "api.call"):
        assert cap in caps, f"manifest capabilities 漏 {cap}"
    # api.list 与 api.call 是两个独立 capability（host/authorization.py:44 逐个校验）。
    # services/cookie.py 的 adapter 探测走 ctx.api.list()，漏声明会直接 PermissionError。
    assert "api.list" in caps, "adapter 探测需要 api.list capability"
    # 日记不再直连第三方 API，openai 依赖应已移除
    dep_names = {d.get("name", "").lower() for d in manifest.get("dependencies", [])}
    assert "openai" not in dep_names, "v4.0 已删除日记直连，不应再依赖 openai"


# ============================================================
# 测试 6. resolve_persona - baseline
# ============================================================


async def test_persona_baseline():
    """没填 self_description + 绘卷 prompt_prefix 为空 → self_description 为空。"""
    p = _make_plugin(art_prompt_prefix="", art_selfie_enabled=False)
    from MaiTrace.services.persona import resolve_persona
    persona = await resolve_persona(p)
    assert persona.nickname == "麦麦"
    assert persona.alias_names == ["小麦"]
    assert "狐妖" in persona.personality
    # 既没用户填、绘卷也没东西 → 应为空
    assert persona.self_description == ""
    # default_style 是主程序 reply_style 原值
    assert persona.default_style == "默认风格"


# ============================================================
# 测试 7. resolve_persona - 用户填 self_description 优先
# ============================================================


async def test_persona_user_self_description():
    p = _make_plugin()
    p.config.persona.self_description = "我是银发红瞳的狐妖"
    from MaiTrace.services.persona import resolve_persona
    persona = await resolve_persona(p)
    # 用户填的优先，绘卷 prompt_prefix 被忽略
    assert persona.self_description == "我是银发红瞳的狐妖", persona.self_description


# ============================================================
# 测试 8. resolve_persona - 绘卷 selfie 自动兜底（无开关）
# ============================================================


async def test_persona_art_fallback_default():
    """self_description 留空 → 自动用绘卷 prompt_prefix，无需任何开关。"""
    p = _make_plugin()  # 默认 art_prompt_prefix='silver hair, red eyes, 1girl'
    from MaiTrace.services.persona import resolve_persona
    persona = await resolve_persona(p)
    assert "silver hair" in persona.self_description, f"绘卷兜底未生效: {persona.self_description!r}"


async def test_persona_reference_image_path():
    """reference_image_path 绝对路径 + 文件存在 → 进 Persona。"""
    test_file = os.path.abspath(__file__)
    p = _make_plugin(art_reference_path=test_file)
    from MaiTrace.services.persona import resolve_persona
    persona = await resolve_persona(p)
    assert persona.reference_image_path == test_file, persona.reference_image_path


async def test_persona_reference_image_missing():
    """reference_image_path 指向不存在的文件 → 安全降级为空。"""
    p = _make_plugin(art_reference_path="/tmp/definitely_not_exist_xxx.jpg")
    from MaiTrace.services.persona import resolve_persona
    persona = await resolve_persona(p)
    assert persona.reference_image_path == ""


# ============================================================
# 测试 9. resolve_persona - multiple_reply_style 抽样
# ============================================================


async def test_persona_style_sampling():
    p = _make_plugin()
    # multiple_probability=0.5，抽 200 次至少有 1 次命中池
    from MaiTrace.services.persona import resolve_persona
    pool = {"撩拨", "克制", "宠溺"}
    hits_default = 0
    hits_pool = 0
    for _ in range(200):
        persona = await resolve_persona(p)
        if persona.style == "默认风格":
            hits_default += 1
        elif persona.style in pool:
            hits_pool += 1
    assert hits_default > 30, f"默认风格命中过少 {hits_default}/200"
    assert hits_pool > 30, f"池命中过少 {hits_pool}/200"

    # 关掉抽样 → 永远默认
    p.config.persona.use_multiple_reply_style = False
    for _ in range(30):
        persona = await resolve_persona(p)
        assert persona.style == "默认风格"


# ============================================================
# 测试 10. resolve_persona - system_prefix 拼接
# ============================================================


async def test_persona_system_prefix():
    p = _make_plugin()
    p.config.persona.self_description = "银发红瞳的狐妖"
    from MaiTrace.services.persona import resolve_persona
    persona = await resolve_persona(p)
    prefix = persona.system_prefix()
    assert "麦麦" in prefix, "system_prefix 应含 nickname"
    assert "小麦" in prefix, "system_prefix 应含 alias"
    assert "银发红瞳的狐妖" in prefix, "system_prefix 应含 self_description"


# ============================================================
# 测试 11. collect_images_for_feed - AI 路径 + self_description 拼 prompt
# ============================================================


async def test_collect_images_ai_path():
    """AI 路径：调绘卷只传场景，selfie_mode=True，默认 selfie_style=photo。"""
    p = _make_plugin()
    p.config.image.enable_image = True
    p.config.image.ai_content_mode = "selfie"
    p.config.image.image_mode = "only_ai"
    p.config.image.image_number = 2
    p.config.image.pic_plugin_model = "model1"
    p.config.image.clear_image = True

    from MaiTrace.services.feed_image import collect_images_for_feed
    images = await collect_images_for_feed(p, "今天泡澡好舒服")
    assert len(images) == 2
    assert all(b == FAKE_PNG for b in images)
    api_calls = p.ctx.api.calls
    assert len(api_calls) == 2
    for c in api_calls:
        assert c["name"] == "1021143806.mais_art_journal.generate_image"
        assert c["kwargs"]["prompt"] == "今天泡澡好舒服"
        assert c["kwargs"]["selfie_mode"] is True
        # 默认 selfie_style=photo（第三人称）
        assert c["kwargs"]["selfie_style"] == "photo"
        # 没传 reference → 不应有 input_image_base64
        assert "input_image_base64" not in c["kwargs"]


async def test_collect_images_img2img_with_reference():
    """传 reference_image_path → 读文件转 base64 + input_image_base64。"""
    p = _make_plugin()
    p.config.image.enable_image = True
    p.config.image.ai_content_mode = "selfie"
    p.config.image.image_mode = "only_ai"
    p.config.image.image_number = 1
    p.config.image.pic_plugin_model = "model1"
    p.config.image.clear_image = True

    ref_path = os.path.abspath(__file__)
    expected_b64 = base64.b64encode(open(ref_path, "rb").read()).decode("ascii")

    from MaiTrace.services.feed_image import collect_images_for_feed
    await collect_images_for_feed(p, "场景", reference_image_path=ref_path)
    call = p.ctx.api.calls[0]
    assert call["kwargs"]["input_image_base64"] == expected_b64
    assert call["kwargs"]["selfie_mode"] is True
    assert call["kwargs"]["selfie_style"] == "photo"


async def test_collect_images_selfie_style_configurable():
    """[image].selfie_style 可改成 standard/mirror。"""
    p = _make_plugin()
    p.config.image.enable_image = True
    p.config.image.ai_content_mode = "selfie"
    p.config.image.image_mode = "only_ai"
    p.config.image.image_number = 1
    p.config.image.pic_plugin_model = "model1"
    p.config.image.clear_image = True
    p.config.image.selfie_style = "mirror"

    from MaiTrace.services.feed_image import collect_images_for_feed
    await collect_images_for_feed(p, "场景")
    assert p.ctx.api.calls[0]["kwargs"]["selfie_style"] == "mirror"


async def test_collect_images_emoji():
    p = _make_plugin()
    p.config.image.enable_image = True
    p.config.image.image_mode = "only_emoji"
    p.config.image.image_number = 3

    from MaiTrace.services.feed_image import collect_images_for_feed
    images = await collect_images_for_feed(p, "哈哈")
    assert len(images) == 3
    assert len(p.ctx.api.calls) == 0


async def test_collect_images_archive():
    p = _make_plugin()
    p.config.image.enable_image = True
    p.config.image.image_mode = "only_ai"
    p.config.image.image_number = 2
    p.config.image.pic_plugin_model = "model1"
    p.config.image.clear_image = False

    archive = p.persistence.get_images_dir()

    from MaiTrace.services.feed_image import collect_images_for_feed
    await collect_images_for_feed(p, "归档测试")
    archived = [f for f in os.listdir(archive) if f.startswith("pic_plugin_")]
    assert len(archived) == 2


async def test_collect_images_disabled():
    p = _make_plugin()
    p.config.image.enable_image = False
    from MaiTrace.services.feed_image import collect_images_for_feed
    images = await collect_images_for_feed(p, "x")
    assert images == []


# ============================================================
# 测试 18. Routine PlanningPluginProvider 三分支
# ============================================================


async def test_planning_provider_has_activity():
    p = _make_plugin()
    payload = {
        "has_activity": True,
        "activity": {
            "name": "工作",
            "description": "在写代码",
            "goal_type": "work",
            "time_window": "09:00-12:00",
        },
        "next_activities": [],
        "as_of": "2026-05-25T10:00:00",
        "timezone": "Asia/Shanghai",
    }
    p._schedule_engine = SimpleNamespace(current_activity_payload=lambda: payload)
    from MaiTrace.services.routine import InternalScheduleProvider, ActivityType
    prov = InternalScheduleProvider(p)
    activity = await prov.get_current_activity()
    assert activity is not None
    assert activity.activity_type == ActivityType.WORKING
    assert "代码" in activity.description
    assert not p.ctx.api.calls, "内部日程不得请求已退役插件"


async def test_planning_provider_no_activity():
    p = _make_plugin()
    p._schedule_engine = SimpleNamespace(current_activity_payload=lambda: {"has_activity": False})
    from MaiTrace.services.routine import InternalScheduleProvider
    prov = InternalScheduleProvider(p)
    activity = await prov.get_current_activity()
    assert activity is None


async def test_planning_provider_api_error():
    p = _make_plugin()
    p._schedule_engine = None
    from MaiTrace.services.routine import InternalScheduleProvider
    prov = InternalScheduleProvider(p)
    activity = await prov.get_current_activity()
    assert activity is None  # 不抛，返回 None


# ============================================================
# 测试 19a-d. Routine 严格决策
# ============================================================


def test_llm_decision_parser():
    """_parse_llm_decision: 严格"是/否"开头 + 解析 reason。"""
    from MaiTrace.services.routine import RoutineRunner
    parse = RoutineRunner._parse_llm_decision

    # 标准格式
    assert parse("是|今天确实想分享") == (True, "今天确实想分享")
    assert parse("否|正在专注工作") == (False, "正在专注工作")
    # 全角分隔符
    assert parse("是｜想发") == (True, "想发")
    # 冒号
    assert parse("否：在睡觉") == (False, "在睡觉")
    # 只有"是"
    assert parse("是") == (True, "")
    # 只有"否"
    assert parse("否") == (False, "")
    # "是的，..." 也算是
    d, r = parse("是的，今天好心情")
    assert d is True and "今天好心情" in r
    # 格式异常：不以"是/否"开头 → 默认拒绝
    d, _ = parse("可能可以发")
    assert d is False
    d, _ = parse("不行")
    assert d is False
    # 空
    assert parse("") == (False, "")
    assert parse("   ") == (False, "")
    # 带引号包裹
    d, r = parse("\"是|想发\"")
    assert d is True and r == "想发"


async def test_hard_block_activity_blacklist():
    """活动黑名单命中 → 直接拒，不调 LLM。"""
    p = _make_plugin()
    # 关掉静默时段干扰（深夜跑时会优先命中 silent_hours 导致 reason 不是黑名单）
    p.config.routine.respect_silent_hours = False
    from MaiTrace.services.routine import RoutineRunner, ActivityInfo, ActivityType
    runner = RoutineRunner(p)

    activity = ActivityInfo(
        activity_type=ActivityType.WORKING,
        description="在写代码",
        time_point="14:00",
    )
    decision = await runner._llm_decide(activity, "post")
    assert decision is False, "WORKING 应被默认黑名单拦"
    hist = runner.get_decision_history()
    assert hist[-1]["hard_blocked"] is True, f"应 hard_blocked=True, 实际={hist[-1]}"
    assert "活动黑名单" in hist[-1]["reason"], f"reason 应含'活动黑名单'，实际={hist[-1]['reason']!r}"


async def test_hard_block_silent_hours():
    """复用 monitor.silent_hours：当前时间在静默区间内 → 拒。"""
    p = _make_plugin()
    p.config.routine.respect_silent_hours = True
    # 把静默时段设为"全天"，保证一定命中
    p.config.monitor.silent_hours = "00:00-23:59"
    p.config.routine.post_blocked_activities = []  # 关掉活动黑名单避免干扰

    from MaiTrace.services.routine import RoutineRunner, ActivityInfo, ActivityType
    runner = RoutineRunner(p)
    activity = ActivityInfo(
        activity_type=ActivityType.RELAXING,  # 不在黑名单
        description="在休息",
        time_point="12:00",
    )
    decision = await runner._llm_decide(activity, "post")
    assert decision is False
    hist = runner.get_decision_history()
    assert hist[-1]["hard_blocked"] is True
    assert "静默时段" in hist[-1]["reason"]

    # 关闭静默卡 post 后，应继续走 LLM（mock 无 ctx.llm，会失败但 hard_blocked 应为 False）
    p.config.routine.respect_silent_hours = False
    await runner._llm_decide(activity, "post")
    hist2 = runner.get_decision_history()
    assert hist2[-1]["hard_blocked"] is False


async def test_max_chance_dice():
    """LLM 通过但 max_post_chance=0 → 二次掷骰永远跳过。"""
    p = _make_plugin()
    p.config.routine.respect_silent_hours = False
    p.config.routine.post_blocked_activities = []
    p.config.routine.max_post_chance = 0.0  # 一定掷骰失败

    from MaiTrace.services.routine import RoutineRunner, ActivityInfo, ActivityType

    # mock 一个会答"是"的 LLM
    class MockLLM:
        async def generate(self, prompt, model="", temperature=0.7, max_tokens=2000, **_):
            return {"success": True, "response": "是|想发"}
    p.ctx.llm = MockLLM()

    runner = RoutineRunner(p)
    activity = ActivityInfo(
        activity_type=ActivityType.RELAXING,
        description="在休息",
        time_point="12:00",
    )
    decision = await runner._llm_decide(activity, "post")
    assert decision is False
    hist = runner.get_decision_history()
    assert hist[-1]["dice_skipped"] is True
    assert hist[-1]["llm_answer"] == "是|想发"

    # max_chance=1.0 → 不掷骰，LLM 通过即真通过
    p.config.routine.max_post_chance = 1.0
    decision2 = await runner._llm_decide(activity, "post")
    assert decision2 is True
    hist2 = runner.get_decision_history()
    assert hist2[-1]["dice_skipped"] is False
    assert hist2[-1]["decision"] is True


# ============================================================
# 测试 20. publish_topic_api 契约：失败 message 不为空
# ============================================================


async def test_admin_check():
    """/zn 命令全局管理员检查：非 admin 一律拒绝，admin 全部放行。"""
    p = _make_plugin()
    from MaiTrace.handlers.commands import dispatch_zn

    # 1. admin_qq 为空 → 所有人都拒
    p.config.plugin.admin_qq = []
    p.ctx.send.sent.clear()
    ok, msg, _ = await dispatch_zn(
        p, matched_groups={"sub": "help"}, stream_id="s1", user_id="100",
    )
    assert ok is False and msg == "no admin"
    assert p.ctx.send.sent and "未配置管理员" in p.ctx.send.sent[-1]["text"]

    # 2. 配了 admin 但调用者不在列表 → 拒
    p.config.plugin.admin_qq = ["123456"]
    p.ctx.send.sent.clear()
    ok, msg, _ = await dispatch_zn(
        p, matched_groups={"sub": "debug help"}, stream_id="s1", user_id="999",
    )
    assert ok is False and msg == "no admin"
    assert "仅管理员可用" in p.ctx.send.sent[-1]["text"]

    # 3. 调用者在 admin 列表 → 通过（help 路径）
    p.ctx.send.sent.clear()
    ok, msg, _ = await dispatch_zn(
        p, matched_groups={"sub": "help"}, stream_id="s1", user_id="123456",
    )
    assert ok is True and msg == "ok"
    # help 文本应已发出
    assert any("/zn <主题>" in m["text"] for m in p.ctx.send.sent)

    # 4. 之前公开的 /zn v 现在也要 admin（关键：旧版任何人都能用）
    p.config.plugin.admin_qq = ["123456"]
    p.ctx.send.sent.clear()
    ok, msg, _ = await dispatch_zn(
        p, matched_groups={"sub": "v 2026-05-25"}, stream_id="s1", user_id="999",
    )
    assert ok is False and msg == "no admin"

    # 5. 不带子命令（"" 进入 help）也要 admin
    p.ctx.send.sent.clear()
    ok, msg, _ = await dispatch_zn(
        p, matched_groups={"sub": ""}, stream_id="s1", user_id="999",
    )
    assert ok is False and msg == "no admin"


def test_is_admin_function():
    """services.permission.is_admin 单元测试。"""
    p = _make_plugin()
    from MaiTrace.services.permission import is_admin

    # 空列表
    p.config.plugin.admin_qq = []
    assert is_admin(p.config, "100") is False
    assert is_admin(p.config, "") is False

    # 列表内
    p.config.plugin.admin_qq = ["123", "456"]
    assert is_admin(p.config, "123") is True
    assert is_admin(p.config, "456") is True
    assert is_admin(p.config, "999") is False
    # 空 qq_account
    assert is_admin(p.config, "") is False
    # 数字字符串边界（int 传入应能正确比较）
    assert is_admin(p.config, 123) is True


# ============================================================
# 测试 21. publish_topic_api 契约：失败 message 不为空
# ============================================================


async def test_publish_topic_api_contract():
    p = _make_plugin()
    # 隔离副作用：mock cookie 流程，避免真实打 napcat HTTP / 扫码登录
    import MaiTrace.services.qzone_publisher as publisher_mod

    async def fake_renew(*args, **kwargs):
        return False  # cookie 失败 → send_feed 早返回

    orig_renew = publisher_mod.renew_cookies
    publisher_mod.renew_cookies = fake_renew
    try:
        from MaiTrace.handlers.apis import publish_topic_api
        result = await publish_topic_api(p, topic="测试", current_activity="")
        assert isinstance(result, dict)
        assert set(result.keys()) >= {"result", "story", "message"}
        assert result["result"] is False  # cookie 失败路径
        assert isinstance(result["message"], str) and result["message"]
        assert "更新 cookies" in result["message"] or "失败" in result["message"]
    finally:
        publisher_mod.renew_cookies = orig_renew


# ============================================================
# 入口
# ============================================================


def main():
    print(f"MaiTrace smoke test ({_PLUGIN_DIR})")
    print("=" * 60)

    print("\n[A] 静态检查")
    _run("syntax: all changed files parse", test_syntax)
    _run("plugin: create_plugin no DeprecationWarning", test_create_plugin_clean)
    _run("components: v6 features and optional messaging hooks", test_components)
    _run("config: original and v6 sections present", test_config_sections)
    _run("config: legacy to current migration", test_migrate_legacy_to_current)
    _run("adapter: napcat-only discovery", test_adapter_discovery_napcat_only)
    _run("adapter: snowluma-only discovery (no config change)", test_adapter_discovery_snowluma_only)
    _run("adapter: adapter_priority respected", test_adapter_priority_respected)
    _run("adapter: fall through + per-adapter stats", test_adapter_falls_through_to_next)
    _run("adapter: none installed → None", test_adapter_none_installed)
    _run("manifest: version 6.2.4 + capabilities", test_manifest)

    print("\n[B] persona 系统")
    _run("persona baseline", test_persona_baseline)
    _run("persona user self_description first", test_persona_user_self_description)
    _run("persona art selfie fallback (default on)", test_persona_art_fallback_default)
    _run("persona reference_image_path absolute", test_persona_reference_image_path)
    _run("persona reference_image missing → empty", test_persona_reference_image_missing)
    _run("persona multiple_reply_style sampling", test_persona_style_sampling)
    _run("persona system_prefix concat", test_persona_system_prefix)

    print("\n[C] 配图生成")
    _run("images: AI path (selfie_mode + photo by default)", test_collect_images_ai_path)
    _run("images: img2img with reference", test_collect_images_img2img_with_reference)
    _run("images: selfie_style configurable", test_collect_images_selfie_style_configurable)
    _run("images: emoji path skips art api", test_collect_images_emoji)
    _run("images: clear_image=False archives", test_collect_images_archive)
    _run("images: enable_image=False empty", test_collect_images_disabled)


    print("\n[E] Routine")
    _run("routine planning has_activity", test_planning_provider_has_activity)
    _run("routine planning no_activity", test_planning_provider_no_activity)
    _run("routine internal engine not initialized", test_planning_provider_api_error)
    _run("routine decision parser strict", test_llm_decision_parser)
    _run("routine hard block: activity blacklist", test_hard_block_activity_blacklist)
    _run("routine hard block: silent hours", test_hard_block_silent_hours)
    _run("routine max_chance dice", test_max_chance_dice)

    print("\n[F] 跨插件 API")
    _run("publish_topic_api contract", test_publish_topic_api_contract)

    print("\n[G] 命令权限")
    _run("is_admin function", test_is_admin_function)
    _run("admin check covers all /zn subcommands", test_admin_check)

    print("\n" + "=" * 60)
    total = len(_passed) + len(_failed)
    print(f"Total: {total}  Passed: {len(_passed)}  Failed: {len(_failed)}")
    if _failed:
        print("\nFailures:")
        for name, msg in _failed:
            print(f"  - {name}: {msg}")
        sys.exit(1)
    print("All passed.")
    sys.exit(0)


if __name__ == "__main__":
    main()
