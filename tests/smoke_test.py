"""MaiTrace 一键离线 smoke 测试。

不需要启动 MaiBot 主程序，全部 mock。覆盖：

- 所有改动文件语法合法
- 插件实例化无 DeprecationWarning
- 5 个组件（zn / send_feed / read_feed / 3 个 @API）正确注册
- config 10 个 section 齐全，含 persona 和 diary.per_message_max_chars
- resolve_persona：4 个 personality 字段 + bot.nickname/alias_names + multiple 抽样 + 绘卷 selfie 兜底
- collect_images_for_feed：self_description 拼进生图 prompt + 返回 bytes + 归档开关
- 表情包路径不调绘卷 API
- TimelineBuilder：per_message_max_chars 截断
- 日记 prompt：含 self_description 行
- Routine PlanningPluginProvider：has_activity / 无活动 / API 异常三分支
- Routine _check_diary 时间窗判定（首次启动 / 已跨越 / 同日不重复）
- send_feed_api / publish_topic_api 调用契约

用法（项目根）：
    .venv/Scripts/python.exe plugins/MaiTrace/tests/smoke_test.py

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

    def set_planning_error(self, exc: Exception):
        self._planning_error = exc

    def set_planning_payload(self, payload: Dict):
        self._planning_payload = payload
        self._planning_error = None

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

    async def generate(self, prompt, model="", temperature=0.7, max_tokens=2000, **kwargs):
        self.calls.append({"prompt": prompt, "model": model, "temperature": temperature})
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


def _make_plugin(**ctx_kwargs):
    """创建插件实例，注入默认配置，挂上 MockCtx。"""
    import MaiTrace.plugin as P  # noqa
    p = P.create_plugin()
    p.set_plugin_config(p.build_default_config())
    p._ctx = MockCtx(**ctx_kwargs)
    return p


# ============================================================
# 测试 1. 全部改动文件 ast.parse
# ============================================================


def test_syntax():
    files = [
        "_manifest.json",  # 用 json.load 验证
        "plugin.py",
        "config.py",
        "handlers/actions.py",
        "handlers/apis.py",
        "handlers/commands.py",
        "services/cookie.py",
        "services/feed_image.py",
        "services/feed_publish.py",
        "services/feed_read.py",
        "services/monitor.py",
        "services/persona.py",
        "services/prompts.py",
        "services/routine.py",
        "services/diary/pipeline.py",
        "services/diary/prompts.py",
        "services/diary/timeline.py",
    ]
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
# 测试 3. 组件注册完整 + 5 个组件齐全
# ============================================================


def test_components():
    import MaiTrace.plugin as P
    inst = P.create_plugin()
    components = inst.get_components()
    names_and_types = {(c["name"], c["type"]) for c in components}
    expected = {
        ("zn", "COMMAND"),
        ("send_feed", "TOOL"),
        ("read_feed", "TOOL"),
        ("send_feed_api", "API"),
        ("get_feeds_list_api", "API"),
        ("publish_topic_api", "API"),
    }
    missing = expected - names_and_types
    extra = names_and_types - expected
    if missing:
        raise AssertionError(f"缺少组件: {missing}")
    if extra:
        raise AssertionError(f"多余组件: {extra}")
    # API 必须 public=True
    for c in components:
        if c["type"] == "API":
            assert c["metadata"].get("public") is True, f"{c['name']} 应该 public=True"


# ============================================================
# 测试 4. config 10 个 section 齐全，persona / diary.per_message_max_chars 在
# ============================================================


def test_config_sections():
    import MaiTrace.plugin as P
    inst = P.create_plugin()
    cfg = inst.build_default_config()
    expected_sections = {
        "plugin", "send", "image", "read", "monitor",
        "routine", "llm", "diary", "persona", "diary_model",
    }
    got = set(cfg.keys())
    if expected_sections - got:
        raise AssertionError(f"缺少 section: {expected_sections - got}")
    # persona 字段
    p = cfg["persona"]
    for k in ("self_description", "use_multiple_reply_style"):
        assert k in p, f"persona 段缺 {k}"
    # diary.per_message_max_chars 默认 200
    assert cfg["diary"]["per_message_max_chars"] == 200, "默认 per_message_max_chars 应为 200"


# ============================================================
# 测试 5. _manifest.json 关键字段
# ============================================================


def test_manifest():
    import json
    manifest = json.loads((_PLUGIN_DIR / "_manifest.json").read_text(encoding="utf-8"))
    assert manifest["version"] == "3.1.0", f"version 应 3.1.0，实际 {manifest['version']}"
    assert manifest["host_application"]["max_version"] != "1.0.0", "host max 不能写死 1.0.0"
    caps = set(manifest["capabilities"])
    for cap in ("chat.get_stream_by_user_id", "api.call"):
        assert cap in caps, f"manifest capabilities 漏 {cap}"


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
    assert persona.reference_image_path == ""
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
    # 用 smoke_test.py 自己当一个"存在的文件"
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
    """AI 路径：调绘卷只传场景（message），且传 selfie_mode=True。"""
    p = _make_plugin()
    p.config.image.enable_image = True
    p.config.image.image_mode = "only_ai"
    p.config.image.image_number = 2
    p.config.image.pic_plugin_model = "model1"
    p.config.image.clear_image = True  # 不归档

    from MaiTrace.services.feed_image import collect_images_for_feed
    images = await collect_images_for_feed(p, "今天泡澡好舒服")
    assert len(images) == 2, f"应返 2 张图，实际 {len(images)}"
    assert all(b == FAKE_PNG for b in images)
    api_calls = p.ctx.api.calls
    assert len(api_calls) == 2
    for c in api_calls:
        assert c["name"] == "1021143806.mais_art_journal.generate_image"
        # prompt 只是场景，不含 self_description / "场景：" 拼接
        assert c["kwargs"]["prompt"] == "今天泡澡好舒服"
        # 始终传 selfie_mode=True 让绘卷走 selfie 流程
        assert c["kwargs"]["selfie_mode"] is True
        assert c["kwargs"]["selfie_style"] == "standard"
        # 不应再传 input_image_base64（绘卷自己读 selfie.reference_image_path）
        assert "input_image_base64" not in c["kwargs"]
        assert "strength" not in c["kwargs"]


async def test_collect_images_emoji():
    p = _make_plugin()
    p.config.image.enable_image = True
    p.config.image.image_mode = "only_emoji"
    p.config.image.image_number = 3

    from MaiTrace.services.feed_image import collect_images_for_feed
    images = await collect_images_for_feed(p, "哈哈")
    assert len(images) == 3
    assert len(p.ctx.api.calls) == 0, "emoji 路径不应调绘卷 API"


async def test_collect_images_archive():
    p = _make_plugin()
    p.config.image.enable_image = True
    p.config.image.image_mode = "only_ai"
    p.config.image.image_number = 2
    p.config.image.pic_plugin_model = "model1"
    p.config.image.clear_image = False  # 归档

    from MaiTrace.services.persistence import get_images_dir
    archive = get_images_dir()
    for f in os.listdir(archive):
        if f.startswith("pic_plugin_") and f.endswith(".png"):
            os.remove(archive / f)

    from MaiTrace.services.feed_image import collect_images_for_feed
    await collect_images_for_feed(p, "归档测试")
    archived = [f for f in os.listdir(archive) if f.startswith("pic_plugin_")]
    assert len(archived) == 2, f"应归档 2 张，实际 {len(archived)}"
    for f in archived:
        os.remove(archive / f)


async def test_collect_images_disabled():
    p = _make_plugin()
    p.config.image.enable_image = False
    from MaiTrace.services.feed_image import collect_images_for_feed
    images = await collect_images_for_feed(p, "x")
    assert images == []


# ============================================================
# 测试 16. TimelineBuilder per_message_max_chars 截断
# ============================================================


def test_timeline_truncate():
    from MaiTrace.services.diary.timeline import TimelineBuilder
    long_text = "x" * 500
    msg = {
        "timestamp": time.time(),
        "processed_plain_text": long_text,
        "message_info": {"user_info": {"user_id": "999", "user_nickname": "他"}},
    }
    # 200 截断
    tb = TimelineBuilder(per_message_max_chars=200)
    result = tb.build([msg])
    assert "x" * 200 + "..." in result, "应该被截断到 200 字 + ..."
    # 0 不截断
    tb2 = TimelineBuilder(per_message_max_chars=0)
    result2 = tb2.build([msg])
    assert "x" * 500 in result2, "0 应该不截断"


# ============================================================
# 测试 17. diary prompts 含 self_description 行
# ============================================================


def test_diary_prompt_has_self_description():
    from MaiTrace.services.diary.prompts import build_diary_prompt, build_qqzone_prompt
    args = dict(
        date="2026-05-25", timeline="X 说: hi",
        date_with_weather="2026年5月25日,星期一,晴。",
        target_length=300, personality_desc="是狐妖",
        style_desc="慵懒优雅", name="麦麦",
    )
    # 带 self_description
    p1 = build_diary_prompt(self_description="银发红瞳", **args)
    assert "关于我的形象" in p1 and "银发红瞳" in p1
    # 不带 → 不出现该行
    p2 = build_diary_prompt(self_description="", **args)
    assert "关于我的形象" not in p2

    p3 = build_qqzone_prompt(self_description="银发红瞳", **args)
    assert "关于我的形象" in p3 and "银发红瞳" in p3


# ============================================================
# 测试 18. Routine PlanningPluginProvider 三分支
# ============================================================


async def test_planning_provider_has_activity():
    p = _make_plugin()
    p.ctx.api.set_planning_payload({
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
    })
    from MaiTrace.services.routine import PlanningPluginProvider, ActivityType
    prov = PlanningPluginProvider(p)
    activity = await prov.get_current_activity()
    assert activity is not None
    assert activity.activity_type == ActivityType.WORKING
    assert "代码" in activity.description


async def test_planning_provider_no_activity():
    p = _make_plugin()
    p.ctx.api.set_planning_payload({"has_activity": False})
    from MaiTrace.services.routine import PlanningPluginProvider
    prov = PlanningPluginProvider(p)
    activity = await prov.get_current_activity()
    assert activity is None


async def test_planning_provider_api_error():
    p = _make_plugin()
    p.ctx.api.set_planning_error(PermissionError("plugin not installed"))
    from MaiTrace.services.routine import PlanningPluginProvider
    prov = PlanningPluginProvider(p)
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
    decision2 = await runner._llm_decide(activity, "post")
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
# 测试 19. Routine._check_diary 时间窗判定（原 19）
# ============================================================


async def test_check_diary_time_window():
    p = _make_plugin()
    p.config.diary.enabled = True
    p.config.diary.schedule_time = "23:30"

    from MaiTrace.services.routine import RoutineRunner
    runner = RoutineRunner(p)

    # 用 monkeypatch 替换 _generate_diary 防止真跑
    triggered = []
    async def fake_generate(*a, **kw):
        triggered.append(time.time())
    runner._generate_diary = fake_generate

    # 场景 1: 首次启动 (last_check_ts=0)，当前已过 23:30 → 应触发
    today = datetime.date.today()
    target_ts = datetime.datetime.combine(today, datetime.time(23, 30)).timestamp()

    # mock datetime.now 太复杂；改成直接调用并断言不抛
    # 我们检查行为：last_diary_date 在触发后应等于 today
    runner._last_check_ts = 0.0
    runner.last_diary_date = None

    # 直接调一次（取决于当前真实时间是否已过 23:30）
    # 改为操纵 schedule_time 让它一定触发：用比当前时间早一分钟的目标
    now = datetime.datetime.now()
    one_min_ago = now - datetime.timedelta(minutes=1)
    p.config.diary.schedule_time = one_min_ago.strftime("%H:%M")

    # 模拟前一轮检查时间在两分钟前
    runner._last_check_ts = (now - datetime.timedelta(minutes=2)).timestamp()
    await runner._check_diary()
    await asyncio.sleep(0.05)  # 等 create_task 跑一下
    assert runner.last_diary_date == today, "跨过 target 应触发"
    assert len(triggered) == 1, f"应触发 1 次，实际 {len(triggered)}"

    # 场景 2: 同一天再次调用 → 不重复触发
    await runner._check_diary()
    await asyncio.sleep(0.05)
    assert len(triggered) == 1, "同一天不重复触发"

    # 场景 3: enabled=False → 不触发
    runner.last_diary_date = None
    p.config.diary.enabled = False
    await runner._check_diary()
    await asyncio.sleep(0.05)
    assert len(triggered) == 1


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
    import MaiTrace.services.feed_publish as fp_mod

    async def fake_renew(*args, **kwargs):
        return False  # cookie 失败 → send_feed 早返回

    orig_renew = fp_mod.renew_cookies
    fp_mod.renew_cookies = fake_renew
    try:
        from MaiTrace.handlers.apis import publish_topic_api
        result = await publish_topic_api(p, topic="测试", current_activity="")
        assert isinstance(result, dict)
        assert set(result.keys()) >= {"result", "story", "message"}
        assert result["result"] is False  # cookie 失败路径
        assert isinstance(result["message"], str) and result["message"]
        assert "更新 cookies" in result["message"] or "失败" in result["message"]
    finally:
        fp_mod.renew_cookies = orig_renew


# ============================================================
# 入口
# ============================================================


def main():
    print(f"MaiTrace smoke test ({_PLUGIN_DIR})")
    print("=" * 60)

    print("\n[A] 静态检查")
    _run("syntax: all changed files parse", test_syntax)
    _run("plugin: create_plugin no DeprecationWarning", test_create_plugin_clean)
    _run("components: 6 components registered", test_components)
    _run("config: 10 sections present", test_config_sections)
    _run("manifest: version 3.1.0 + capabilities", test_manifest)

    print("\n[B] persona 系统")
    _run("persona baseline", test_persona_baseline)
    _run("persona user self_description first", test_persona_user_self_description)
    _run("persona art selfie fallback (default on)", test_persona_art_fallback_default)
    _run("persona reference_image_path absolute", test_persona_reference_image_path)
    _run("persona reference_image missing → empty", test_persona_reference_image_missing)
    _run("persona multiple_reply_style sampling", test_persona_style_sampling)
    _run("persona system_prefix concat", test_persona_system_prefix)

    print("\n[C] 配图生成")
    _run("images: AI path (selfie_mode + scene only)", test_collect_images_ai_path)
    _run("images: emoji path skips art api", test_collect_images_emoji)
    _run("images: clear_image=False archives", test_collect_images_archive)
    _run("images: enable_image=False empty", test_collect_images_disabled)

    print("\n[D] 日记")
    _run("diary timeline per_message_max_chars truncate", test_timeline_truncate)
    _run("diary prompt has self_description line", test_diary_prompt_has_self_description)

    print("\n[E] Routine")
    _run("routine planning has_activity", test_planning_provider_has_activity)
    _run("routine planning no_activity", test_planning_provider_no_activity)
    _run("routine planning api_error returns None", test_planning_provider_api_error)
    _run("routine decision parser strict", test_llm_decision_parser)
    _run("routine hard block: activity blacklist", test_hard_block_activity_blacklist)
    _run("routine hard block: silent hours", test_hard_block_silent_hours)
    _run("routine max_chance dice", test_max_chance_dice)
    _run("routine _check_diary time window", test_check_diary_time_window)

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
