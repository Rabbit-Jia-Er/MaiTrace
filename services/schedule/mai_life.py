"""通过 Mai_life 的公开查询 API 提供内置格式的日程。

复用 ScheduleEngine 的消费者接口及人格加载，不创建内置数据库、生成器或
日程主动任务。RPC 只发生在后台刷新和显式查询中，Hook 继续读取内存快照。
"""

from __future__ import annotations

from datetime import datetime, timedelta
from html import escape
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo
import asyncio
import time

from ...utils import get_global_str, get_logger
from . import ScheduleEngine
from .query import CurrentActivity
from .snapshot import ActivitySnapshot, ScheduleSnapshotCache
from .utils.stream_filter import is_stream_allowed
from .utils.timezone_manager import TimezoneManager


logger = get_logger(__name__)
PROVIDER_ID = 'maibot-community.mai-life'
_API_TIMEOUT_SECONDS = 8
_MANAGED_BY_PROVIDER = '当前日程由 Mai_life（麦生活）提供，生成、修改、删除和清理请使用麦生活的对应功能。'
_KINDS = {'sleep': 'sleep', 'nap': 'sleep', 'rest': 'rest', 'study': 'study',
          'work': 'work', 'leisure': 'relaxing', 'meal': 'meal', 'travel': 'commuting'}


class MaiLifeScheduleSource(ScheduleEngine):
    """日程来源边界：将 maibot.schedule v1 转为 MaiTrace 内置快照与区间。"""

    def __init__(self, plugin: Any, **kwargs: Any) -> None:
        super().__init__(plugin, **kwargs)
        self._external_snapshot: Optional[ActivitySnapshot] = None
        self._provider_info: Optional[Dict[str, Any]] = None
        self._error: Optional[str] = 'snapshot_not_ready'
        self._refresh_lock = asyncio.Lock()

    async def start(self) -> None:
        async with self._lifecycle_lock:
            if self._started or not self._schedule_config().enabled:
                return
            self._timezone_manager = TimezoneManager(self._config_timezone())
            self._started = True
            # 提供方可能在 MaiTrace 之后加载；记录失败并由协调循环重试。
            await self.refresh(force=True)

    async def stop(self) -> None:
        async with self._lifecycle_lock:
            self._started = False
            pending = [task for task in self._operations if task is not asyncio.current_task()]
            for task in pending:
                task.cancel()
            if pending:
                await asyncio.gather(*pending, return_exceptions=True)
            self._operations.clear()
            self._external_snapshot = None
            self._provider_info = None
            self._timezone_manager = None
            self._error = 'snapshot_not_ready'

    async def _call(self, name: str, **args: Any) -> Dict[str, Any]:
        # SDK 将 Host 的 result 解包；此处收到的是麦生活的业务信封。
        try:
            async with asyncio.timeout(_API_TIMEOUT_SECONDS):
                response = await self.plugin.ctx.call_capability(
                    'api.call', timeout_ms=_API_TIMEOUT_SECONDS * 1000, api_name=f'{PROVIDER_ID}.{name}',
                    version='1', args=args,
                )
        except TimeoutError as exc:
            raise TimeoutError(f'Mai_life {name} 查询超时') from exc
        if not isinstance(response, dict) or response.get('success') is not True:
            error = response.get('error', '无效的 API 返回值') if isinstance(response, dict) else '无效的 API 返回值'
            raise RuntimeError(f'Mai_life {name} 查询失败: {error}')
        data = response.get('data')
        if not isinstance(data, dict):
            raise ValueError(f'Mai_life {name} 缺少 data 对象')
        return data

    async def _ensure_provider(self) -> None:
        if self._provider_info is not None:
            return
        info = await self._call('get_schedule_provider_info')
        if (info.get('provider_id') != PROVIDER_ID or info.get('protocol') != 'maibot.schedule'
                or info.get('protocol_version') != 1 or info.get('api_version') != '1'
                or info.get('scope') != 'global'):
            raise ValueError('Mai_life 日程提供方协议不兼容（需要 maibot.schedule v1、global）')
        capabilities = info.get('capabilities')
        if not isinstance(capabilities, list) or not {'snapshot', 'timeline'}.issubset(capabilities):
            raise ValueError('Mai_life 未声明快照和时间线查询能力')
        if info.get('max_query_hours') != 72:
            raise ValueError('Mai_life 日程时间线需要支持 72 小时查询')
        ZoneInfo(info['timezone'])
        self._provider_info = info

    @staticmethod
    def _activity(item: Any, timezone: str) -> CurrentActivity:
        if not isinstance(item, dict):
            raise ValueError('Mai_life 活动必须是对象')
        for field in ('id', 'kind', 'name', 'start_at', 'end_at'):
            if not isinstance(item.get(field), str) or not item[field]:
                raise ValueError(f'Mai_life 活动缺少 {field}')
        if item['kind'] not in _KINDS:
            raise ValueError(f"Mai_life 未知活动类型: {item['kind']}")
        tz = ZoneInfo(timezone)
        start, end = datetime.fromisoformat(item['start_at']), datetime.fromisoformat(item['end_at'])
        if start.tzinfo is None or end.tzinfo is None or end <= start:
            raise ValueError('Mai_life 活动需要带时区且递增的起止时间')
        start, end = start.astimezone(tz), end.astimezone(tz)
        parameters = {}
        for source, target in (('location', 'location'), ('scene', 'scene_hint')):
            value = item.get(source, '')
            if not isinstance(value, str):
                raise ValueError(f'Mai_life 活动 {source} 必须是文本')
            if value:
                parameters[target] = value
        description = item.get('description', '')
        if not isinstance(description, str):
            raise ValueError('Mai_life 活动 description 必须是文本')
        return CurrentActivity(
            name=item['name'], description=description or item['name'],
            goal_type=_KINDS[item['kind']], goal_id=item['id'],
            time_window=f'{start:%H:%M}-{end:%H:%M}', parameters=parameters,
            start_at=start, end_at=end,
        )

    async def refresh(self, *, force: bool = False) -> Optional[ActivitySnapshot]:
        if not self._started or not self._schedule_config().enabled:
            return None
        with self._track_operation():
            async with self._refresh_lock:
                cached = self.peek()
                if cached is not None and not force and time.monotonic() - cached.refreshed_at < min(30, self._config_cache_ttl()):
                    return cached
                try:
                    await self._ensure_provider()
                    data = await self._call('get_schedule_snapshot',
                        future_limit=min(20, self._schedule_config().max_future_activities))
                    # 外部时刻保留含义，显示与日历边界仍以 MaiTrace 配置为准。
                    ZoneInfo(data['timezone'])
                    timezone = self.timezone_manager.timezone_str
                    as_of = datetime.fromisoformat(data['as_of'])
                    if as_of.tzinfo is None:
                        raise ValueError('Mai_life 快照时刻缺少时区')
                    as_of = as_of.astimezone(ZoneInfo(timezone))
                    if not isinstance(data.get('schedule_available'), bool):
                        raise ValueError('Mai_life 快照缺少 schedule_available')
                    current = self._activity(data['current'], timezone) if data.get('current') is not None else None
                    upcoming_data = data.get('next_activities')
                    if not isinstance(upcoming_data, list):
                        raise ValueError('Mai_life 快照缺少 next_activities 列表')
                    upcoming = sorted((self._activity(item, timezone) for item in upcoming_data), key=lambda item: item.start_at)
                    if not data['schedule_available'] and (current is not None or upcoming):
                        raise ValueError('Mai_life 空日程快照包含活动')
                    if current is not None and not current.start_at <= as_of < current.end_at:
                        raise ValueError('Mai_life 当前活动与快照时刻不一致')
                    self._external_snapshot = ActivitySnapshot(
                        current=current, next_activities=tuple((item.start_at.strftime('%H:%M'), item.name) for item in upcoming),
                        next_activity_details=tuple(upcoming), as_of=as_of.isoformat(),
                        timezone=timezone, refreshed_at=time.monotonic(),
                    )
                    self._error = None
                except Exception as exc:
                    self._external_snapshot = None
                    self._error = f'Mai_life 日程读取失败: {exc}'
                    logger.error('%s', self._error, exc_info=True)
                return self.peek()

    def peek(self) -> Optional[ActivitySnapshot]:
        snapshot = self._external_snapshot
        if not self._started or not self._schedule_config().enabled or snapshot is None:
            return None
        if time.monotonic() - snapshot.refreshed_at >= self._config_cache_ttl():
            return None
        if snapshot.current is not None and not snapshot.current.start_at <= self.timezone_manager.get_now() < snapshot.current.end_at:
            return None
        return snapshot

    def current_activity_payload(self) -> Dict[str, Any]:
        snapshot = self.peek()
        payload = ScheduleSnapshotCache.to_api_payload(snapshot)
        if snapshot is None:
            payload['timezone'] = self._timezone_manager.timezone_str if self._timezone_manager is not None else self._config_timezone()
            payload['error'] = self._error or 'snapshot_stale'
        return payload

    async def activity_segments(self, start: datetime, end: datetime) -> List[Dict[str, Any]]:
        if not self._started or not self._schedule_config().enabled:
            return []
        with self._track_operation():
            await self._ensure_provider()
            tz = self.timezone_manager.get_now().tzinfo
            start = start.replace(tzinfo=tz) if start.tzinfo is None else start.astimezone(tz)
            end = end.replace(tzinfo=tz) if end.tzinfo is None else end.astimezone(tz)
            if end <= start:
                return []
            start = max(start, end - timedelta(hours=72))
            data = await self._call('get_schedule_timeline', start_at=start.isoformat(), end_at=end.isoformat())
            ZoneInfo(data['timezone'])
            timezone = self.timezone_manager.timezone_str
            if not isinstance(data.get('activities'), list) or not isinstance(data.get('missing_days'), list):
                raise ValueError('Mai_life 时间线缺少 activities/missing_days 列表')
            activities = [self._activity(item, timezone) for item in data['activities']]
            return sorted(({'start': item.start_at, 'end': item.end_at,
                            'name': item.name, 'description': item.description,
                            'goal_type': item.goal_type, 'goal_id': item.goal_id}
                           for item in activities if item.start_at < end and item.end_at > start),
                          key=lambda item: (item['start'], item['end']))

    def build_generator(self) -> Any:
        raise RuntimeError(_MANAGED_BY_PROVIDER)

    async def patrol_proactive(self) -> None:
        """外部日程的主动任务和维护由 Mai_life 管理。"""

    async def handle_tool(self, tool_name: str, **kwargs: Any) -> Dict[str, Any]:
        if not self._started or not self._schedule_config().enabled:
            return {'type': 'error', 'content': '日程未启用或尚未就绪'}
        stream_id = str(kwargs.get('stream_id') or kwargs.get('chat_id') or '')
        stream_info = kwargs.get('stream_info')
        if not isinstance(stream_info, dict):
            stream_info = {key: kwargs.get(key, '') for key in ('platform', 'group_id', 'user_id')}
        if stream_id and not is_stream_allowed(stream_id, self._schedule_config().allowed_streams, stream_info):
            return {'type': 'error', 'content': '当前聊天未启用日程功能'}
        if tool_name in {'generate_schedule', 'update_schedule'}:
            return {'type': 'error', 'content': _MANAGED_BY_PROVIDER}
        if tool_name != 'get_planning_status':
            return {'type': 'error', 'content': f'未知日程工具: {tool_name}'}
        with self._track_operation():
            try:
                await self.refresh(force=True)
                if self._error:
                    raise RuntimeError(self._error)
                now = self.timezone_manager.get_now()
                start = now.replace(hour=0, minute=0, second=0, microsecond=0)
                segments = await self.activity_segments(start, start + timedelta(days=1))
                rows = [f"{item['start']:%H:%M}-{item['end']:%H:%M} {item['name']}" for item in segments]
                content = f'📅 今日日程 {now:%Y-%m-%d}（Mai_life）\n' + ('\n'.join(rows) if rows else '今天还没有日程，由麦生活负责生成。')
                return {'type': 'planning_status', 'content': content,
                        'data': [{key: value.isoformat() if isinstance(value, datetime) else value
                                  for key, value in item.items()} for item in segments]}
            except Exception as exc:
                return {'type': 'error', 'content': f'日程查询失败: {exc}'}

    async def handle_command(self, content: str, stream_id: str = '', user_id: str = '',
                             platform: str = 'qq', **kwargs: Any) -> Tuple[bool, str, bool]:
        cfg = self._schedule_config()
        info = {'platform': platform, 'user_id': user_id, 'group_id': kwargs.get('group_id', '')}
        if cfg.admin_users and user_id not in cfg.admin_users:
            success, message = False, '当前用户没有日程管理权限'
        elif not is_stream_allowed(stream_id, cfg.allowed_streams, info):
            success, message = False, '当前聊天未启用日程功能'
        else:
            command = content.strip()
            for prefix in ('/zn plan', '/plan', 'plan'):
                if command == prefix or command.startswith(prefix + ' '):
                    command = command[len(prefix):].strip()
                    break
            action = command.partition(' ')[0] or 'status'
            if action in {'generate', 'regenerate', 'update', 'delete', 'clear'}:
                success, message = False, _MANAGED_BY_PROVIDER
            elif action == 'help':
                success, message = True, '/zn plan status：查看麦生活日程\n/zn plan list：查看日程图片\n' + _MANAGED_BY_PROVIDER
            elif action in {'status', 'list'}:
                result = await self.handle_tool('get_planning_status', detailed=True, stream_id=stream_id, stream_info=info)
                success, message = result['type'] != 'error', result['content']
                if success and action == 'list' and result['data']:
                    with self._track_operation():
                        try:
                            await self._show_image(stream_id, result['data'])
                            return True, '已发送日程图片', True
                        except Exception as exc:
                            success, message = False, f'日程图片发送失败: {exc}'
            else:
                success, message = False, f'未知日程命令: {action}；使用 /zn plan help 查看帮助'
        if stream_id:
            await self.plugin.ctx.send.text(message, stream_id)
        return success, message, True

    async def _show_image(self, stream_id: str, segments: List[Dict[str, Any]]) -> None:
        if not stream_id:
            raise ValueError('缺少发送日程图片的聊天流')
        bot_name = (await get_global_str(self.plugin.ctx, 'bot.nickname')).strip()
        rows = []
        for item in segments:
            start, end = datetime.fromisoformat(item['start']), datetime.fromisoformat(item['end'])
            rows.append(f"<tr><td>{start:%H:%M}—{end:%H:%M}</td><td>{escape(item['name'])}</td></tr>")
        html = ("<!doctype html><html lang='zh-CN'><meta charset='utf-8'>"
                "<style>body{padding:28px;background:#f7f3ea;font-family:'Microsoft YaHei',sans-serif}"
                "table{width:100%;border-collapse:collapse}td{padding:14px;border-bottom:1px solid #ddd}</style>"
                f"<h1>{escape(bot_name)}的一天</h1><p>{self.timezone_manager.get_now():%Y-%m-%d} · Mai_life</p>"
                f"<table>{''.join(rows)}</table></html>")
        result = await self.plugin.ctx.render.html2png(html, viewport={'width': 900, 'height': 600},
            full_page=True, allow_network=False, render_timeout_ms=20000)
        if not isinstance(result, dict) or result.get('success') is False or not result.get('image_base64'):
            raise RuntimeError('日程图片渲染失败')
        if await self.plugin.ctx.send.image(result['image_base64'], stream_id) is False:
            raise RuntimeError('日程图片发送失败')
