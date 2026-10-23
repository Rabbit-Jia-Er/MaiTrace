"""MaiTrace（麦时迹）插件入口

业务逻辑全部抽到 services/，命令/动作/API 在 handlers/。
plugin.py 只负责：装配生命周期、声明 @Command/@Tool/@API 装饰器。
"""

from __future__ import annotations

from collections.abc import Mapping
from concurrent.futures import Future
from datetime import datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar, Dict, List, Optional, Tuple
import asyncio
import tomllib

from maibot_sdk import (
    API,
    Command,
    HomeCard,
    HookHandler,
    MaiBotPlugin,
    PluginConfigBase,
    Tool,
)
from maibot_sdk.types import HookMode, HookOrder, ToolParameterInfo, ToolParamType

from .config import MaiTracePluginConfig
from .config.migration import migrate_legacy_config, rebuild_upgrade_config
from .services.persistence import PluginPersistence, queue_legacy_config_archive
from .utils import get_logger

if TYPE_CHECKING:
    from .services.inject import InjectionEngine
    from .services.life import LifeEngine
    from .services.messaging import MessagingController
    from .services.schedule import ScheduleEngine

logger = get_logger(__name__)


def _tool_error(message: str) -> Dict[str, Any]:
    """Host 依据 success 判断工具结果，不能只填写旧格式的 type=error。"""
    return {"success": False, "type": "error", "content": message, "error": message}


class MaiTracePlugin(MaiBotPlugin):
    """MaiTrace：每日生活、临时约定、私人日记与 QQ 空间。"""

    config_model: ClassVar[type[PluginConfigBase] | None] = MaiTracePluginConfig
    config_reload_subscriptions = ("bot", "model")

    # 类型注解（实际属性在 __init__ 中初始化）
    _routine: Optional[Any]

    def __init__(self) -> None:
        super().__init__()
        self._routine = None
        self._persistence: Optional[PluginPersistence] = None
        self._schedule_engine: Optional["ScheduleEngine"] = None
        self._life_engine: Optional["LifeEngine"] = None
        self._injection: Optional["InjectionEngine"] = None
        self._messaging: Optional["MessagingController"] = None
        self._runtime_task: Optional[asyncio.Task] = None
        self._messaging_registered = False
        self._ready = False
        self._runtime_lock = asyncio.Lock()
        self._routine_state: Dict[str, Any] = {}
        self._registered_deadline_signature: Optional[Tuple[Any, ...]] = None
        self._pending_config_upgrade: Optional[Tuple[Dict[str, Any], Dict[str, Any]]] = None
        self._legacy_config_snapshot: Optional[bytes] = None
        self._legacy_config_archive: Optional[Future[None]] = None

    @property
    def plugin_dir(self) -> Path:
        return Path(__file__).resolve().parent

    @property
    def persistence(self) -> PluginPersistence:
        if self._persistence is None:
            self._persistence = PluginPersistence(self.ctx.paths.data_dir)
        return self._persistence

    @property
    def schedule_engine(self) -> Optional["ScheduleEngine"]:
        return self._schedule_engine

    @property
    def life_engine(self) -> Optional["LifeEngine"]:
        return self._life_engine

    def get_default_config(self) -> Dict[str, Any]:
        """在宿主重建旧配置前，保存跨段迁移需要的原始值。"""
        defaults = super().get_default_config()
        self._pending_config_upgrade = None
        try:
            raw_config = (self.plugin_dir / "config.toml").read_bytes()
            source = tomllib.loads(raw_config.decode("utf-8"))
        except FileNotFoundError:
            return defaults
        section = source.get("plugin", {})
        if isinstance(section, dict) and section.get("config_version") != defaults["plugin"]["config_version"]:
            if self._legacy_config_snapshot != raw_config or self._legacy_config_archive is None:
                # 激活时已有 SDK 路径；冷配置校验尚无上下文，先保留到自身旧数据目录。
                # 直接提交不可变字节，宿主随后的整文件重写不会改变线程要归档的内容。
                data_dir = self.ctx.paths.data_dir if self._ctx is not None else self.plugin_dir / "data"
                self._legacy_config_archive = queue_legacy_config_archive(data_dir, raw_config)
            self._legacy_config_snapshot = raw_config
            migrated, changed = migrate_legacy_config(source)
            if changed:
                # 当前 Host 先按默认结构删除废弃字段，再调用 normalize_plugin_config。
                # 只对与这份磁盘配置重建结果完全一致的输入应用快照，避免覆盖 WebUI 编辑。
                self._pending_config_upgrade = (
                    rebuild_upgrade_config(defaults, source),
                    rebuild_upgrade_config(defaults, migrated),
                )
        return defaults

    def normalize_plugin_config(
        self,
        config_data: Mapping[str, Any] | None,
    ) -> Tuple[Dict[str, Any], bool]:
        """升级 MaiTrace 旧配置，再交给当前 SDK 补齐字段和校验。"""
        source = dict(config_data) if isinstance(config_data, Mapping) else {}
        raw = source
        if self._pending_config_upgrade is not None and source == self._pending_config_upgrade[0]:
            raw = self._pending_config_upgrade[1]
            self._pending_config_upgrade = None
        migrated, _ = migrate_legacy_config(raw)
        normalized, changed = super().normalize_plugin_config(migrated)
        normalized["plugin"]["config_version"] = self.build_default_config()["plugin"]["config_version"]
        return normalized, changed or normalized != source

    def get_components(self) -> List[Dict[str, Any]]:
        components = super().get_components()
        self._messaging_registered = bool(self.config.messaging.enabled)
        message_hooks = {"maitrace_receive", "maitrace_before_send", "maitrace_after_send"}
        if not self._messaging_registered:
            components = [entry for entry in components if entry["name"] not in message_hooks]
        # 等待全部图片以及显式重试。不能只扩大生图 RPC、却让最外层命令先超时。
        from .services.limits import feed_read_budget_seconds, publication_budget_seconds
        publish_seconds = publication_budget_seconds(self.config)
        read_seconds = feed_read_budget_seconds(self.config)
        schedule = self.config.schedule
        rounds = schedule.max_rounds if schedule.use_multi_round else 1
        # 角色裁判与生成前状态推断也会请求模型，每一轮都有自己的 RPC 预算。
        schedule_seconds = (rounds + 2) * (schedule.generation_timeout + 10) + 120
        for component in components:
            if component["name"] in ("zn", "send_feed", "publish_topic_api"):
                component["metadata"]["timeout_ms"] = int(max(
                    publish_seconds,
                    schedule_seconds,
                ) * 1000)
            elif component["name"] == "read_feed":
                component["metadata"]["timeout_ms"] = int(read_seconds * 1000)
            elif component["name"].removesuffix("_v6") in (
                "get_planning_status", "generate_schedule", "update_schedule",
            ):
                component["metadata"]["timeout_ms"] = int(schedule_seconds * 1000)
        self._registered_deadline_signature = self._deadline_signature()
        return components

    def _deadline_signature(self) -> Tuple[Any, ...]:
        return (
            self.config.image.image_number, self.config.image.image_retry,
            self.config.llm.llm_timeout_seconds,
            self.config.read.read_number,
            self.config.schedule.generation_timeout, self.config.schedule.use_multi_round,
            self.config.schedule.max_rounds,
        )

    # ===== 生命周期 =====

    async def _archive_pending_config(self) -> None:
        """等待已提交的原件归档，并确保冷配置校验的快照进入 SDK 数据目录。"""
        if self._legacy_config_archive is not None:
            pending = asyncio.wrap_future(self._legacy_config_archive)
            try:
                await asyncio.shield(pending)
            except asyncio.CancelledError:
                await pending
                raise
            self._legacy_config_archive = None
        if self._legacy_config_snapshot is not None:
            await self.persistence.archive_legacy_config(self._legacy_config_snapshot)
            self._legacy_config_snapshot = None

    async def on_load(self) -> None:
        await self._archive_pending_config()
        if not self.config.plugin.enabled:
            self.ctx.logger.info("MaiTrace 已禁用（plugin.enabled=false）")
            return

        from .services.runtime import start_runtime
        async with self._runtime_lock:
            await start_runtime(self)
        self.ctx.logger.info("MaiTrace 6.2 已加载：每日作息、生活与空间服务已就绪")

    async def on_unload(self) -> None:
        from .services.runtime import stop_runtime
        async with self._runtime_lock:
            await stop_runtime(self)
        self.ctx.logger.info("MaiTrace 已卸载")

    async def on_config_update(
        self,
        scope: str,
        config_data: dict[str, Any],
        version: str,
    ) -> None:
        """重建实例服务，等待旧任务退出；Hook 注册集合在插件重载时更新。"""
        del config_data
        from .services.runtime import start_runtime, stop_runtime
        if scope == "self" and self.config.messaging.enabled != self._messaging_registered:
            self.ctx.logger.warning("生活消息钩子开关已更新，请重载插件以调整 Hook 注册集合")
        if scope == "self" and self._registered_deadline_signature != self._deadline_signature():
            self.ctx.logger.warning("请求预算配置已更新，请重载插件以同步 Host 的组件超时")
        async with self._runtime_lock:
            await stop_runtime(self)
            await start_runtime(self)
        self.ctx.logger.info("MaiTrace 配置更新: scope=%s version=%s", scope, version)


    # ===== Command / Tool / API 组件（具体逻辑分发到 handlers/） =====

    @Command(
        "zn",
        description="MaiTrace 统一命令：/zn help 查看用法",
        pattern=r"^\s*/zn(?:\s+(?P<sub>.+))?\s*$",
    )
    async def handle_zn(self, **kwargs: Any) -> tuple:
        from .handlers.commands import dispatch_zn
        return await dispatch_zn(self, **kwargs)

    @Tool(
        "send_feed",
        description="发一条相应主题的说说到 QQ 空间，自动配图并返回已发布的正文。",
        detailed_description=(
            "适用场景：用户要求发说说、有人希望你更新 QQ 空间、或你自行判断适合发说说时。"
            "典型触发词：说说 / 空间 / 动态。"
        ),
        parameters=[
            ToolParameterInfo(
                name="topic",
                param_type=ToolParamType.STRING,
                description="要发送的说说主题或完整内容",
                required=True,
            ),
            ToolParameterInfo(
                name="user_name",
                param_type=ToolParamType.STRING,
                description="要求你发说说的好友的 QQ 名称",
                required=False,
            ),
        ],
    )
    async def handle_send_feed(self, **kwargs: Any) -> Dict[str, Any]:
        from .handlers.actions import execute_send_feed
        return await execute_send_feed(self, **kwargs)

    @Tool(
        "read_feed",
        description="读取指定好友最近的 QQ 空间动态，返回动态内容并按配置评论点赞；enable_comment=false 时不发表评论。",
        detailed_description=(
            "适用场景：需要查看某人动态/说说/QQ 空间，或有人希望你评价某人的空间内容时。"
            "典型触发词：说说 / 空间 / 动态。"
            "若用户只要看不要评，传入 enable_comment=false；点赞仍按配置执行。"
        ),
        parameters=[
            ToolParameterInfo(
                name="target_name",
                param_type=ToolParamType.STRING,
                description="需要阅读动态的好友的 QQ 名称",
                required=True,
            ),
            ToolParameterInfo(
                name="user_name",
                param_type=ToolParamType.STRING,
                description="要求你阅读动态的好友的 QQ 名称",
                required=False,
            ),
            ToolParameterInfo(
                name="enable_comment",
                param_type=ToolParamType.STRING,
                description="是否评论：true/false；false、否、不回复=不发表评论，点赞仍按配置执行。默认 true",
                required=False,
            ),
        ],
    )
    async def handle_read_feed(self, **kwargs: Any) -> Dict[str, Any]:
        from .handlers.actions import execute_read_feed
        return await execute_read_feed(self, **kwargs)

    @Tool(
        "recall_qzone_activity",
        description="查询自己在 QQ 空间发布、评论和回复过的内容。被问及空间活动时先查记录再回答。",
        parameters=[
            ToolParameterInfo(name="keyword", param_type=ToolParamType.STRING, description="可选的人名、QQ、动态ID或话题关键词", required=False),
            ToolParameterInfo(name="limit", param_type=ToolParamType.STRING, description="返回条数，默认10，范围1到50", required=False),
        ],
        visibility="visible",
    )
    async def handle_recall_qzone_activity(self, **kwargs: Any) -> Dict[str, Any]:
        from .handlers.actions import execute_recall_qzone_activity
        return await execute_recall_qzone_activity(self, **kwargs)

    @API(
        "send_feed_api",
        description="发送一条说说到 QQ 空间。参数：message(str, 必填)、images(list[str] base64, 可选)。",
        version="1",
        public=True,
        timeout_ms=180000,
    )
    async def handle_send_feed_api(
        self,
        message: str = "",
        images: Optional[list[str]] = None,
        **kwargs: Any,
    ) -> dict:
        del kwargs
        from .handlers.apis import send_feed_api
        return await send_feed_api(self, message=message, images=images)

    @API(
        "get_feeds_list_api",
        description="获取指定 QQ 的最近说说列表。参数：target_qq(str, 必填)、num(int, 默认5)。",
        version="1",
        public=True,
    )
    async def handle_get_feeds_list_api(
        self,
        target_qq: str = "",
        num: int = 5,
        filter: bool = False,
        **kwargs: Any,
    ) -> dict:
        del kwargs
        from .handlers.apis import get_feeds_list_api
        return await get_feeds_list_api(self, target_qq=target_qq, num=num, filter=filter)

    @API(
        "publish_topic_api",
        description=(
            "完整发说说流程：LLM 按主题生成正文 + 自动配图 + 发布。"
            "参数：topic(str, 必填，传 'custom' 走私聊取词模式)、current_activity(str, 可选)。"
            "返回 {result, story, message}。"
        ),
        version="1",
        public=True,
    )
    async def handle_publish_topic_api(
        self,
        topic: str = "",
        current_activity: str = "",
        activity_type: str = "",
        outfit: str = "",
        **kwargs: Any,
    ) -> dict:
        del kwargs
        from .handlers.apis import publish_topic_api
        return await publish_topic_api(
            self,
            topic=topic,
            current_activity=current_activity,
            activity_type=activity_type,
            outfit=outfit,
        )

    @Tool(
        "get_planning_status_v6",
        description="查看今日日程安排，按时间顺序显示正在进行和即将到来的活动",
        parameters=[
            ToolParameterInfo(
                name="detailed",
                param_type=ToolParamType.BOOLEAN,
                description="是否显示详细信息",
                required=False,
            ),
        ],
    )
    async def handle_get_planning_status(self, **kwargs: Any) -> Dict[str, Any]:
        return await self._run_schedule_tool('get_planning_status', kwargs)

    @Tool(
        "generate_schedule_v6",
        description="生成并应用麦麦今天的生活作息，承接近期经历与已确认约定。已有日程时直接返回。",
        parameters=[],
    )
    async def handle_generate_schedule(self, **kwargs: Any) -> Dict[str, Any]:
        return await self._run_schedule_tool("generate_schedule", kwargs)

    @Tool(
        "update_schedule_v6",
        description="按人设判断是否接受约定：今天只插入空闲时段，保留所有原活动；明天、后天、大后天可预约，冲突时协商其他时间。",
        parameters=[
            ToolParameterInfo(
                name="description",
                param_type=ToolParamType.STRING,
                description="约定的日期、时间、事情及可选时长（如\"今天14:00聊半小时\"、\"下周一18:00一起吃饭\"），未来日期不限制天数",
                required=True,
            ),
        ],
    )
    async def handle_update_schedule(self, **kwargs: Any) -> Dict[str, Any]:
        return await self._run_schedule_tool('update_schedule', kwargs)

    async def _run_schedule_tool(self, name: str, kwargs: Dict[str, Any]) -> Dict[str, Any]:
        if self._schedule_engine is None or not self.config.schedule.enabled:
            return _tool_error("内部日程尚未就绪或已关闭")
        result = await self._schedule_engine.handle_tool(name, **kwargs)
        if result.get("type") == "error" or result.get("success") is False:
            return {**result, **_tool_error(str(result.get("error") or result.get("content", "日程工具执行失败")))}
        return result

    @API("get_current_activity", description="当前内部日程快照，供绘卷等插件查询", public=True, timeout_ms=10000)
    async def handle_get_current_activity(self, chat_id: str = "global", **kwargs: Any) -> Dict[str, Any]:
        del chat_id, kwargs  # 日程始终是麦麦的一条全局时间线。
        if not self.config.schedule.enabled or self._schedule_engine is None:
            return {"has_activity": False, "activity": None, "next_activities": [],
                    "as_of": "", "timezone": self.config.schedule.timezone,
                    "error": "schedule_disabled" if not self.config.schedule.enabled else "plugin_not_initialized"}
        return self._schedule_engine.current_activity_payload()

    @API("get_schedule", description="获取指定日期的内部活动时间线", public=True, timeout_ms=10000)
    async def handle_get_schedule(self, day: str = "", **kwargs: Any) -> List[Dict[str, Any]]:
        del kwargs
        if not self.config.schedule.enabled or self._schedule_engine is None or not self._schedule_engine.started:
            return []
        now = self._schedule_engine.timezone_manager.get_now()
        date = datetime.strptime(day, "%Y-%m-%d").date() if day else now.date()
        start = datetime.combine(date, datetime.min.time(), tzinfo=now.tzinfo)
        segments = await self._schedule_engine.activity_segments(start, start + timedelta(days=1))
        # RPC 载荷只包含可序列化值，时区信息保留在 ISO 时间中。
        return [{key: value.isoformat() if isinstance(value, datetime) else value
                 for key, value in segment.items()} for segment in segments]

    @API("get_life_state", description="获取精力、心情与睡眠等全局生活状态", public=True, timeout_ms=10000)
    async def handle_get_life_state(self, **kwargs: Any) -> Dict[str, Any]:
        del kwargs
        return self._life_engine.peek_state() if self._life_engine is not None else {}

    @API("get_current_scene", description="获取当前细化生活场景", public=True, timeout_ms=10000)
    async def handle_get_current_scene(self, **kwargs: Any) -> Dict[str, Any]:
        del kwargs
        return self._life_engine.current_scene() if self._life_engine is not None else {}

    @API("get_environment_snapshot", description="获取本地历法与时间快照", public=True, timeout_ms=10000)
    async def handle_get_environment(self, **kwargs: Any) -> Dict[str, Any]:
        del kwargs
        return self._life_engine.environment_snapshot() if self._life_engine is not None else {}

    @API("create_proactive_opportunity", description="创建外部分享契机，由调度器判断是否消费", public=True)
    async def handle_create_opportunity(self, topic: str = "", motive: str = "", **kwargs: Any) -> Dict[str, Any]:
        if self._life_engine is None:
            return {"success": False, "error": "生活服务未启用"}
        return await self._life_engine.add_external_opportunity(
            topic=topic, motive=motive, privacy=str(kwargs.get("privacy", "private")),
            expires_minutes=int(kwargs.get("expires_minutes", 120)),
            source_key=str(kwargs.get("source_key", "")),
        )

    @HookHandler("maisaka.planner.before_request", name="maitrace_planner_life",
                 mode=HookMode.BLOCKING, order=HookOrder.LATE, timeout_ms=6000)
    async def handle_planner_life(self, **kwargs: Any) -> Dict[str, Any]:
        if self._messaging is not None:
            self._messaging.track_planner_task(str(kwargs.get("session_id", "")), kwargs.get("items", []))
        if self._injection is None:
            return {"action": "continue"}
        return self._injection.planner(kwargs)

    @HookHandler("maisaka.replyer.before_request", name="maitrace_replyer_life",
                 mode=HookMode.BLOCKING, order=HookOrder.LATE, timeout_ms=6000)
    async def handle_replyer_life(self, **kwargs: Any) -> Dict[str, Any]:
        if self._messaging is not None:
            self._messaging.track_reply_anchor(**kwargs)
        if self._injection is None:
            return {"action": "continue"}
        return self._injection.replyer(kwargs)

    @HookHandler("chat.receive.before_process", name="maitrace_receive",
                 mode=HookMode.BLOCKING, order=HookOrder.LATE, timeout_ms=6000)
    async def handle_receive(self, **kwargs: Any) -> Dict[str, Any]:
        if self._messaging is None or not self.config.messaging.enabled:
            return {"action": "continue"}
        return await self._messaging.on_receive(**kwargs)

    @HookHandler("send_service.before_send", name="maitrace_before_send",
                 mode=HookMode.BLOCKING, order=HookOrder.LATE, timeout_ms=5000)
    async def handle_before_send(self, **kwargs: Any) -> Dict[str, Any]:
        if self._messaging is None or not self.config.messaging.enabled:
            return {"action": "continue"}
        return await self._messaging.on_send_before(**kwargs)

    @HookHandler("send_service.after_send", name="maitrace_after_send",
                 mode=HookMode.OBSERVE, order=HookOrder.LATE, timeout_ms=5000)
    async def handle_after_send(self, **kwargs: Any) -> Dict[str, Any]:
        # 热关闭期间仍收口已经发出的消息，不能丢掉主动额度的成功确认。
        if self._messaging is not None:
            await self._messaging.on_send_after(**kwargs)
        return {"action": "continue"}

    @HomeCard("maitrace_life", "麦时迹", content=(
        "麦麦的日程、生活与 QQ 空间。\n\n"
        "`/zn plan` 查看日程，`/zn status` 查看生活状态，`/zn help` 查看全部命令。"
    ), icon="calendar", order=40)
    async def handle_home_card(self, **kwargs: Any) -> Dict[str, Any]:
        del kwargs
        return {"title": "麦时迹"}


def create_plugin() -> MaiTracePlugin:
    """工厂函数：Runner 通过此函数实例化插件。"""
    return MaiTracePlugin()
