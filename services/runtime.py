"""MaiTrace 实例生命周期与单一协调循环。

日程由所选提供方维护，生活层只消费这条时间线，Routine 消费公开契机。
任何初始化失败都明确上抛，并关闭已经创建的线程、数据库与后台任务。
"""

from typing import Any, Awaitable, List, Set
import asyncio
import time

from .inject import InjectionEngine
from .life import LifeEngine
from .messaging import MessagingController
from .routine import RoutineRunner
from .schedule import ScheduleEngine
from .schedule.mai_life import MaiLifeScheduleSource


async def resolve_injection_streams(plugin: Any) -> None:
    """通过 SDK 解析已有真实聊天流；Hook 本身只查询结果集合。"""
    entries = plugin.config.schedule.allowed_streams
    if not entries or any(entry.strip().lower() == "all" for entry in entries):
        plugin._injection.allowed_streams = None
        return
    resolved: Set[str] = set()
    for raw in entries:
        entry = raw.strip()
        if not entry:
            continue
        if entry.startswith("session:"):
            resolved.add(entry.split(":", 1)[1])
            continue
        parts = entry.split(":", 2)
        if len(parts) == 3 and parts[1] in ("group", "private"):
            platform, chat_type, target_id = parts
            if chat_type == "group":
                stream = await plugin.ctx.chat.get_stream_by_group_id(target_id, platform)
            else:
                stream = await plugin.ctx.chat.get_stream_by_user_id(target_id, platform)
            if not isinstance(stream, dict) or not stream.get("session_id"):
                plugin.ctx.logger.warning("注入白名单未找到已存在的聊天流: %s", entry)
                continue
            resolved.add(str(stream["session_id"]))
        else:
            resolved.add(entry)
    plugin._injection.allowed_streams = resolved


async def coordinate(plugin: Any) -> None:
    """统一驱动快照、生活推进和原规划主动能力；注入不做这些工作。"""
    scopes_refreshed = 0.0
    while True:
        try:
            await plugin._schedule_engine.refresh()
            if plugin._life_engine is not None:
                snapshot = plugin._schedule_engine.current_activity_payload()
                if not plugin.config.schedule.enabled or not snapshot.get("error"):
                    await plugin._life_engine.advance(snapshot)
                plugin._schedule_engine.update_life_state(plugin._life_engine.peek_state(),
                    managed_private_streams=plugin._life_engine.managed_private_streams())
            else:
                plugin._schedule_engine.update_life_state({})
            await plugin._schedule_engine.patrol_proactive()
            if plugin._injection is not None and time.monotonic() - scopes_refreshed >= 300:
                await resolve_injection_streams(plugin)
                scopes_refreshed = time.monotonic()
        except asyncio.CancelledError:
            raise
        except Exception:
            plugin.ctx.logger.exception("日程与生活协调循环失败，下个周期重试")
        await asyncio.sleep(30)


async def start_runtime(plugin: Any) -> None:
    if not plugin.config.plugin.enabled:
        return
    data_dir = plugin.persistence.data_dir
    try:
        await plugin._archive_pending_config()
        copied = await plugin.persistence.migrate_legacy_data(plugin.plugin_dir)
        if copied:
            plugin.ctx.logger.info("MaiTrace 旧数据迁移完成，共复制 %s 个文件", copied)
        source = MaiLifeScheduleSource if plugin.config.schedule.provider == "maibot-community.mai-life" else ScheduleEngine
        plugin._schedule_engine = source(plugin, data_dir=data_dir / "schedule")
        await plugin._schedule_engine.start()
        if plugin.config.life.enabled:
            plugin._life_engine = LifeEngine(
                plugin, data_dir=data_dir / "life", schedule_engine=plugin._schedule_engine,
            )
            await plugin._life_engine.start()
            snapshot = plugin._schedule_engine.current_activity_payload()
            if not plugin.config.schedule.enabled or not snapshot.get("error"):
                await plugin._life_engine.advance(snapshot)
            plugin._schedule_engine.update_life_state(plugin._life_engine.peek_state(),
                managed_private_streams=plugin._life_engine.managed_private_streams())
        plugin._injection = InjectionEngine(plugin)
        if plugin.config.messaging.enabled and plugin._messaging_registered:
            plugin._messaging = MessagingController(plugin, life_engine=plugin._life_engine)
            await plugin._messaging.start()
        plugin._runtime_task = asyncio.create_task(coordinate(plugin), name="maitrace-coordinate")
        plugin._routine = RoutineRunner(plugin)
        plugin._routine.restore_state(plugin._routine_state)
        await plugin._routine.start()
        plugin._ready = True
    except BaseException:
        try:
            await stop_runtime(plugin)
        except Exception:
            plugin.ctx.logger.exception("初始化失败后的清理也发生异常")
        raise


async def stop_runtime(plugin: Any) -> None:
    """即使调用方取消或某个服务关闭失败，也完成其余服务的清理。"""
    cleanup = asyncio.create_task(_stop_runtime(plugin), name="maitrace-cleanup")
    try:
        await asyncio.shield(cleanup)
    except asyncio.CancelledError:
        await cleanup
        raise


async def _stop_runtime(plugin: Any) -> None:
    plugin._ready = False
    errors: List[Exception] = []

    async def close(name: str, operation: Awaitable[Any]) -> None:
        try:
            await operation
        except Exception as exc:
            plugin.ctx.logger.exception("关闭 %s 失败", name)
            errors.append(exc)

    # 先停止消费者，防止关闭数据库后仍有图片完成回调或契机确认。
    if plugin._routine is not None:
        await close("空间调度", plugin._routine.stop())
        plugin._routine_state = plugin._routine.export_state()
        plugin._routine = None
    if plugin._runtime_task is not None:
        plugin._runtime_task.cancel()
        await asyncio.gather(plugin._runtime_task, return_exceptions=True)
        plugin._runtime_task = None
    if plugin._messaging is not None:
        await close("消息管理", plugin._messaging.stop())
        plugin._messaging = None
    if plugin._life_engine is not None:
        await close("生活服务", plugin._life_engine.stop())
        plugin._life_engine = None
    if plugin._schedule_engine is not None:
        await close("日程服务", plugin._schedule_engine.stop())
        plugin._schedule_engine = None
    plugin._injection = None
    if errors:
        raise ExceptionGroup("MaiTrace 服务关闭失败", errors)
