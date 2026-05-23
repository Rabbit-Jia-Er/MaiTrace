"""MaiTrace（麦麦空间）插件入口 — 新 SDK 版本

业务逻辑全部抽到 services/，命令/动作/API 在 handlers/。
plugin.py 只负责：装配生命周期、声明 @Command/@Action/@API 装饰器。
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from collections.abc import Mapping
from typing import Any, ClassVar, Optional

from maibot_sdk import (
    API,
    Action,
    Command,
    Field,
    MaiBotPlugin,
    PluginConfigBase,
    Tool,
)
from maibot_sdk.types import ActivationType, ToolParameterInfo, ToolParamType

from .config import MaiTracePluginConfig

logger = logging.getLogger("maitrace.plugin")


# ===== 配置迁移：v3.0 → v3.1 =====
# v3.1 把 [send] 中的图片相关字段拆到新段 [image]，把 [models] 改名 [llm] 并把图片字段移到 [image]。
# 拼写矫正字段 (like/comment_possibility, self_readnum) 通过 AliasChoices 在 config.py 中向后兼容，
# 此处只处理"跨段搬迁"——pydantic v2 alias 不能跨 section。
_IMAGE_FIELDS_FROM_SEND = (
    "enable_image",
    "image_mode",
    "ai_probability",
    "image_number",
    "pic_plugin_model",
)
_IMAGE_FIELDS_FROM_LLM = ("image_prompt", "clear_image")
_LLM_FIELDS = ("text_model", "llm_timeout_seconds", "show_prompt")


def _migrate_v30_to_v31(raw_config: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    """把旧 [send].image_* / [models].* 搬到 [image] / [llm]。返回 (新 config, 是否变更)。"""
    changed = False
    cfg = dict(raw_config)

    send = dict(cfg.get("send") or {})
    models = dict(cfg.get("models") or {})
    image = dict(cfg.get("image") or {})
    llm = dict(cfg.get("llm") or {})

    # 1) [send].image_* → [image]
    for field in _IMAGE_FIELDS_FROM_SEND:
        if field in send and field not in image:
            image[field] = send.pop(field)
            changed = True
        elif field in send:
            # [image] 已有，删除旧位置避免重复
            send.pop(field, None)
            changed = True

    # 2) [models].image_prompt / clear_image → [image]
    for field in _IMAGE_FIELDS_FROM_LLM:
        if field in models and field not in image:
            image[field] = models.pop(field)
            changed = True
        elif field in models:
            models.pop(field, None)
            changed = True

    # 3) [models] 剩余字段 → [llm]
    for field in _LLM_FIELDS:
        if field in models and field not in llm:
            llm[field] = models.pop(field)
            changed = True
        elif field in models:
            models.pop(field, None)
            changed = True

    # 4) 整段 [models] 清理（剩余的未知字段也丢弃，因为 SDK 会因 extra="ignore" 静默丢）
    if "models" in cfg:
        cfg.pop("models", None)
        changed = True

    # 写回各段
    if send:
        cfg["send"] = send
    if image:
        cfg["image"] = image
    if llm:
        cfg["llm"] = llm

    return cfg, changed


class MaiTracePlugin(MaiBotPlugin):
    """MaiTrace：QQ 空间发说说 / 刷空间 / 日记 / 日程驱动。"""

    config_model: ClassVar[type[PluginConfigBase] | None] = MaiTracePluginConfig

    # 类型注解（实际属性在 __init__ 中初始化）
    _routine: Optional[Any]
    _migrated_data: bool

    def __init__(self) -> None:
        super().__init__()
        self._routine = None
        self._migrated_data = False

    # ===== 配置迁移 hook（SDK 在校验前调用） =====

    def normalize_plugin_config(
        self,
        config_data: Mapping[str, Any] | None,
    ) -> tuple[dict[str, Any], bool]:
        """v3.0 → v3.1 配置迁移 + 委托 SDK 默认归一化。

        SDK 会在 config_data 与默认配置之间 merge，且 extra="ignore" 会丢弃未声明字段。
        所以这里必须**在 super 之前**把旧位置字段搬到新位置。
        """
        raw = dict(config_data) if isinstance(config_data, Mapping) else {}
        migrated, did_migrate = _migrate_v30_to_v31(raw) if raw else (raw, False)
        if did_migrate:
            logger.info("检测到 v3.0 旧配置，已迁移 [send] 图片字段到 [image]、[models] 改名 [llm]")

        normalized, default_changed = super().normalize_plugin_config(migrated)
        return normalized, did_migrate or default_changed

    # ===== 生命周期 =====

    async def on_load(self) -> None:
        if not self.config.plugin.enabled:
            self.ctx.logger.info("MaiTrace 已禁用（plugin.enabled=false）")
            return

        # 迁移旧路径数据（一次性）
        await self._migrate_legacy_data()

        # 启动 Routine（延迟 10s 等其他插件就绪）
        try:
            from .services.routine import RoutineRunner
            self._routine = RoutineRunner(self)
            asyncio.create_task(self._delayed_start_routine())
        except ImportError as exc:
            self.ctx.logger.warning("RoutineRunner 未就绪，跳过日程驱动: %s", exc)

        self.ctx.logger.info("MaiTrace v3 已加载")

    async def _delayed_start_routine(self) -> None:
        await asyncio.sleep(10)
        if self._routine is not None:
            try:
                await self._routine.start()
            except Exception as exc:
                self.ctx.logger.error("启动 Routine 失败: %s", exc, exc_info=True)

    async def on_unload(self) -> None:
        if self._routine is not None:
            with contextlib.suppress(Exception):
                await self._routine.stop()
        self.ctx.logger.info("MaiTrace 已卸载")

    async def on_config_update(
        self,
        scope: str,
        config_data: dict[str, Any],
        version: str,
    ) -> None:
        """配置热重载：仅记录，运行时配置由 services 在调用时实时读取。"""
        del config_data
        self.ctx.logger.info("MaiTrace 配置更新: scope=%s version=%s", scope, version)

    # ===== 数据迁移（一次性） =====

    async def _migrate_legacy_data(self) -> None:
        """把旧的 processed_list / cookies / qrcode 文件搬到 data/ 下。"""
        if self._migrated_data:
            return
        import os
        import shutil

        plugin_dir = os.path.dirname(os.path.abspath(__file__))
        data_dir = os.path.join(plugin_dir, "data")
        os.makedirs(data_dir, exist_ok=True)

        moves = [
            (os.path.join(plugin_dir, "processed_list.json"), os.path.join(data_dir, "processed_list.json")),
            (os.path.join(plugin_dir, "processed_comments.json"), os.path.join(data_dir, "processed_comments.json")),
        ]
        # cookies-*.json 在 qzone/ 下
        qzone_dir = os.path.join(plugin_dir, "qzone")
        if os.path.isdir(qzone_dir):
            for fname in os.listdir(qzone_dir):
                if fname.startswith("cookies-") and fname.endswith(".json"):
                    moves.append((os.path.join(qzone_dir, fname), os.path.join(data_dir, fname)))
                elif fname == "qrcode.png":
                    moves.append((os.path.join(qzone_dir, fname), os.path.join(data_dir, fname)))

        for src, dst in moves:
            if os.path.exists(src) and not os.path.exists(dst):
                try:
                    shutil.move(src, dst)
                    self.ctx.logger.info("迁移数据文件: %s → %s", src, dst)
                except Exception as exc:
                    self.ctx.logger.warning("迁移 %s 失败: %s", src, exc)

        self._migrated_data = True

    # ===== Command / Action / Tool 组件（具体逻辑分发到 handlers/） =====

    @Command(
        "zn",
        description="MaiTrace 统一命令：/zn help 查看用法",
        pattern=r"^\s*/zn(?:\s+(?P<sub>.+))?\s*$",
    )
    async def handle_zn(self, **kwargs: Any) -> tuple:
        from .handlers.commands import dispatch_zn
        return await dispatch_zn(self, **kwargs)

    @Action(
        "send_feed",
        description="发一条相应主题的说说（包含图片，自带回复）",
        activation_type=ActivationType.KEYWORD,
        activation_keywords=["说说", "空间", "动态"],
        action_parameters={
            "topic": "要发送的说说主题或完整内容",
            "user_name": "要求你发说说的好友的 QQ 名称",
        },
        action_require=[
            "用户要求发说说时使用",
            "当有人希望你更新 QQ 空间时使用",
            "当你认为适合发说说时使用",
        ],
        associated_types=["text"],
    )
    async def handle_send_feed(self, **kwargs: Any) -> tuple:
        from .handlers.actions import execute_send_feed
        return await execute_send_feed(self, **kwargs)

    @Action(
        "read_feed",
        description="读取好友最近的动态/说说并评论点赞（自带回复）",
        activation_type=ActivationType.KEYWORD,
        activation_keywords=["说说", "空间", "动态"],
        action_parameters={
            "target_name": "需要阅读动态的好友的 QQ 名称",
            "user_name": "要求你阅读动态的好友的 QQ 名称",
        },
        action_require=[
            "需要阅读某人动态/说说/QQ空间时使用",
            "当有人希望你评价某人的动态/说说/QQ空间",
            "当你认为适合阅读说说时使用",
        ],
        associated_types=["text"],
    )
    async def handle_read_feed(self, **kwargs: Any) -> tuple:
        from .handlers.actions import execute_read_feed
        return await execute_read_feed(self, **kwargs)

    @API(
        "send_feed_api",
        description="发送一条说说到 QQ 空间。参数：message(str, 必填)、images(list[bytes], 可选)。",
        version="1",
        public=True,
    )
    async def handle_send_feed_api(
        self,
        message: str = "",
        images: Optional[list[bytes]] = None,
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
        **kwargs: Any,
    ) -> dict:
        del kwargs
        from .handlers.apis import get_feeds_list_api
        return await get_feeds_list_api(self, target_qq=target_qq, num=num)


def create_plugin() -> MaiTracePlugin:
    """工厂函数：Runner 通过此函数实例化插件。"""
    return MaiTracePlugin()
