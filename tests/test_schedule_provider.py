"""可选日程提供方的真实 SDK、生命周期与消费者回归，不访问外部服务。"""

from copy import deepcopy
from datetime import datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import IsolatedAsyncioTestCase, TestCase
from unittest.mock import patch
from zoneinfo import ZoneInfo
import asyncio
import sys

from maibot_sdk.context import PluginContext, PluginPaths

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from MaiTrace.plugin import MaiTracePlugin
from MaiTrace.services.activity import ActivityType
from MaiTrace.services.routine import InternalScheduleProvider
from MaiTrace.services.runtime import start_runtime, stop_runtime
from MaiTrace.services.schedule import ScheduleEngine
from MaiTrace.services.schedule.config import ScheduleSection
from MaiTrace.services.schedule.mai_life import MaiLifeScheduleSource, PROVIDER_ID


class LocalPlugin(MaiTracePlugin):
    def __init__(self, directory: Path) -> None:
        super().__init__()
        self.directory = directory

    @property
    def plugin_dir(self) -> Path:
        return self.directory


class ProviderRPC:
    """替换传输，保留 SDK 的 api.call 参数编码及 Host result 解包。"""

    def __init__(self) -> None:
        self.calls = []
        self.business_failure = set()
        self.rpc_failure = set()
        self.block_snapshot = False
        self.entered = asyncio.Event()
        self.now = datetime.now(ZoneInfo('Asia/Tokyo')).replace(microsecond=0)
        self.current = self.activity('a1', 'study', '图书馆自习', self.now - timedelta(minutes=30), self.now + timedelta(hours=1))
        self.next = self.activity('a2', 'meal', '午饭', self.now + timedelta(hours=1), self.now + timedelta(hours=2))
        self.info = {'provider_id': PROVIDER_ID, 'protocol': 'maibot.schedule', 'protocol_version': 1,
                     'api_version': '1', 'scope': 'global', 'timezone': 'Asia/Tokyo',
                     'capabilities': ['snapshot', 'timeline'], 'max_query_hours': 72}
        self.snapshot = {'timezone': 'Asia/Tokyo', 'as_of': self.now.isoformat(), 'revision': 'rev1',
                         'schedule_available': True, 'current': self.current, 'next_activities': [self.next]}
        self.timeline = {'timezone': 'Asia/Tokyo', 'activities': [self.current, self.next], 'missing_days': []}

    @staticmethod
    def activity(identifier, kind, name, start, end):
        return {'id': identifier, 'kind': kind, 'name': name, 'description': '',
                'start_at': start.isoformat(), 'end_at': end.isoformat(),
                'location': '图书馆', 'scene': '坐在窗边整理笔记' if identifier == 'a1' else ''}

    async def __call__(self, method, plugin_id, payload, **kwargs):
        if method != 'cap.call' or plugin_id != 'Rabbit-Jia-Er.MaiTrace':
            raise AssertionError('必须经 SDK 调用')
        capability, args = payload['capability'], payload['args']
        self.calls.append({**payload, **kwargs})
        if capability == 'api.call':
            name = args['api_name'].removeprefix(PROVIDER_ID + '.')
            if args['version'] != '1' or name == args['api_name']:
                raise AssertionError('日程 API 名称或版本错误')
            if name in self.rpc_failure:
                return {'success': False, 'error': '提供方未加载'}
            if name in self.business_failure:
                return {'success': True, 'result': {'success': False, 'error': '日程服务尚未初始化', 'data': None}}
            if name == 'get_schedule_snapshot' and self.block_snapshot:
                self.entered.set()
                await asyncio.Event().wait()
            data = {'get_schedule_provider_info': self.info, 'get_schedule_snapshot': self.snapshot,
                    'get_schedule_timeline': self.timeline}[name]
            return {'success': True, 'result': {'success': True, 'error': None, 'data': deepcopy(data)}}
        if capability == 'config.get':
            values = {'bot.nickname': '测试麦', 'bot.qq_account': '999', 'bot.alias_names': []}
            return {'success': True, 'value': values.get(args['key'], args.get('default'))}
        if capability == 'chat.get_all_streams':
            return {'success': True, 'streams': []}
        if capability in {'send.text', 'send.image'}:
            return {'success': True}
        if capability == 'render.html2png':
            return {'success': True, 'result': {'success': True, 'image_base64': 'aW1hZ2U='}}
        raise AssertionError(f'外部日程模式不应调用 {capability}')

    def api_calls(self, name=None):
        return [call for call in self.calls if call['capability'] == 'api.call'
                and (name is None or call['args']['api_name'] == PROVIDER_ID + '.' + name)]


class ProviderConfigTests(TestCase):
    def test_default_is_internal_and_invalid_provider_is_rejected(self):
        self.assertEqual(ScheduleSection().provider, 'internal')
        with self.assertRaises(ValueError):
            ScheduleSection(provider='unknown')


class ProviderTests(IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = TemporaryDirectory(prefix='maitrace-provider-')
        self.directory = Path(self.temp.name)
        self.plugin = LocalPlugin(self.directory)
        self.plugin.set_plugin_config(self.plugin.build_default_config())
        self.plugin.config.schedule.provider = PROVIDER_ID
        self.plugin.config.schedule.auto_schedule_enabled = False
        self.plugin.config.schedule.auto_infer_next_day_prompt = False
        self.plugin.config.environment.calendar_enabled = False
        self.plugin.config.life.scene_enabled = False
        self.plugin.config.life.dream_enabled = False
        self.plugin.config.life.diary_enabled = False
        self.plugin.config.routine.post_enabled = False
        self.plugin.config.routine.browse_enabled = False
        self.rpc = ProviderRPC()
        self.plugin._set_context(PluginContext('Rabbit-Jia-Er.MaiTrace', rpc_call=self.rpc,
            paths=PluginPaths(data_dir=self.directory / 'sdk-data', runtime_dir=self.directory / 'sdk-runtime')))
        self.source = MaiLifeScheduleSource(self.plugin, data_dir=self.directory / 'schedule')

    async def asyncTearDown(self):
        await stop_runtime(self.plugin)
        await self.source.stop()
        self.temp.cleanup()

    async def test_snapshot_maps_to_internal_payload_and_hooks_do_not_call_rpc(self):
        self.plugin.config.schedule.auto_schedule_enabled = True
        await self.source.start()
        self.plugin._schedule_engine = self.source
        payload = await self.plugin.handle_get_current_activity()
        self.assertTrue(payload['has_activity'])
        self.assertEqual(payload['timezone'], 'Asia/Shanghai')
        self.assertEqual(payload['activity']['goal_id'], 'a1')
        self.assertEqual(payload['activity']['description'], '图书馆自习')
        self.assertEqual(payload['activity']['parameters'], {'location': '图书馆', 'scene_hint': '坐在窗边整理笔记'})
        self.assertEqual(datetime.fromisoformat(payload['activity']['start_at']), datetime.fromisoformat(self.rpc.current['start_at']))
        self.assertTrue(payload['activity']['start_at'].endswith('+08:00'))
        calls = len(self.rpc.calls)
        activity = await InternalScheduleProvider(self.plugin).get_current_activity()
        self.assertEqual(activity.activity_type, ActivityType.STUDYING)
        for _ in range(10):
            self.assertEqual(self.source.peek().current.name, '图书馆自习')
        self.assertEqual(len(self.rpc.calls), calls)
        self.assertIsNone(self.source._worker)
        self.assertIsNone(self.source._scheduler)
        self.assertFalse((self.directory / 'schedule').exists())

    async def test_timeline_uses_72_hour_limit_and_preserves_original_bounds(self):
        await self.source.start()
        earlier = self.rpc.activity('old', 'nap', '午休', self.rpc.now - timedelta(days=1), self.rpc.now - timedelta(days=1) + timedelta(hours=1))
        self.rpc.timeline['activities'].append(earlier)
        self.rpc.timeline['missing_days'] = [(self.rpc.now - timedelta(days=2)).date().isoformat()]
        end = self.rpc.now + timedelta(minutes=10)
        segments = await self.source.activity_segments(end - timedelta(days=5), end)
        self.assertEqual([item['goal_id'] for item in segments], ['old', 'a1'])
        self.assertEqual(segments[0]['goal_type'], 'sleep')
        self.assertEqual(segments[1]['end'], datetime.fromisoformat(self.rpc.current['end_at']))
        args = self.rpc.api_calls('get_schedule_timeline')[-1]['args']['args']
        self.assertEqual(datetime.fromisoformat(args['end_at']) - datetime.fromisoformat(args['start_at']), timedelta(hours=72))
        self.plugin._schedule_engine = self.source
        timeline = await self.plugin.handle_get_schedule(day=self.source.timezone_manager.get_now().strftime('%Y-%m-%d'))
        self.assertTrue(all(isinstance(item['start'], str) and isinstance(item['end'], str) for item in timeline))

    async def test_provider_activity_types_work_with_existing_routine(self):
        await self.source.start()
        self.plugin._schedule_engine = self.source
        self.rpc.current['name'] = '普通活动'
        reader = InternalScheduleProvider(self.plugin)
        for kind, expected in (
            ('sleep', ActivityType.SLEEPING), ('nap', ActivityType.SLEEPING),
            ('rest', ActivityType.RELAXING), ('leisure', ActivityType.RELAXING),
            ('study', ActivityType.STUDYING), ('work', ActivityType.WORKING),
            ('meal', ActivityType.EATING), ('travel', ActivityType.COMMUTING),
        ):
            with self.subTest(kind=kind):
                self.rpc.current['kind'] = kind
                await self.source.refresh(force=True)
                self.assertEqual((await reader.get_current_activity()).activity_type, expected)

    async def test_disabled_external_schedule_never_calls_provider(self):
        self.plugin.config.schedule.enabled = False
        await self.source.start()
        self.plugin._schedule_engine = self.source
        self.assertIsNone(await InternalScheduleProvider(self.plugin).get_current_activity())
        self.assertEqual(await self.plugin.handle_get_schedule(), [])
        self.assertEqual((await self.plugin.handle_get_current_activity())['error'], 'schedule_disabled')
        self.assertEqual(self.rpc.calls, [])

    async def test_empty_day_never_generates_and_write_commands_never_call_rpc(self):
        self.rpc.snapshot.update(schedule_available=False, current=None, next_activities=[], revision='')
        self.rpc.timeline['activities'] = []
        await self.source.start()
        self.assertIsNone(self.source.current_activity_payload()['error'])
        self.assertFalse(self.source.current_activity_payload()['has_activity'])
        result = await self.source.handle_tool('get_planning_status')
        self.assertEqual(result['data'], [])
        self.assertIn('麦生活负责生成', result['content'])
        calls = len(self.rpc.calls)
        for action in ('generate', 'regenerate', 'update', 'delete', 'clear'):
            success, message, _ = await self.source.handle_command(action)
            self.assertFalse(success)
            self.assertIn('Mai_life', message)
        self.plugin._schedule_engine = self.source
        for name in ('generate_schedule', 'update_schedule'):
            result = await self.plugin._run_schedule_tool(name, {})
            self.assertFalse(result['success'])
        self.assertEqual(len(self.rpc.calls), calls)

    async def test_rpc_and_business_failures_clear_snapshot_and_recover(self):
        await self.source.start()
        for failures in (self.rpc.rpc_failure, self.rpc.business_failure):
            failures.add('get_schedule_snapshot')
            with self.assertLogs('plugin.Rabbit-Jia-Er.MaiTrace.mai_life', level='ERROR'):
                await self.source.refresh(force=True)
            self.assertIsNone(self.source.peek())
            self.assertIn('失败', self.source.current_activity_payload()['error'])
            failures.clear()
            await self.source.refresh(force=True)
            self.assertTrue(self.source.current_activity_payload()['has_activity'])

    async def test_incompatible_protocol_and_invalid_activity_are_visible(self):
        self.rpc.info['protocol_version'] = 2
        with self.assertLogs('plugin.Rabbit-Jia-Er.MaiTrace.mai_life', level='ERROR'):
            await self.source.start()
        self.assertIn('协议不兼容', self.source.current_activity_payload()['error'])
        self.rpc.info['protocol_version'] = 1
        self.rpc.current['kind'] = 'unknown'
        with self.assertLogs('plugin.Rabbit-Jia-Er.MaiTrace.mai_life', level='ERROR'):
            await self.source.refresh(force=True)
        self.assertIn('未知活动类型', self.source.current_activity_payload()['error'])

    async def test_cache_expires_and_stop_cancels_pending_rpc(self):
        await self.source.start()
        with patch('MaiTrace.services.schedule.mai_life.time.monotonic', return_value=self.source.peek().refreshed_at + self.plugin.config.schedule.cache_ttl + 1):
            self.assertIsNone(self.source.peek())
            self.assertEqual(self.source.current_activity_payload()['error'], 'snapshot_stale')
        self.rpc.block_snapshot = True
        task = asyncio.create_task(self.source.refresh(force=True))
        await asyncio.wait_for(self.rpc.entered.wait(), timeout=1)
        await asyncio.wait_for(self.source.stop(), timeout=1)
        self.assertTrue(task.cancelled())
        self.assertIsNone(self.source.peek())

    async def test_slow_provider_times_out_and_can_recover(self):
        self.rpc.block_snapshot = True
        with patch('MaiTrace.services.schedule.mai_life._API_TIMEOUT_SECONDS', 0.01):
            with self.assertLogs('plugin.Rabbit-Jia-Er.MaiTrace.mai_life', level='ERROR'):
                await asyncio.wait_for(self.source.start(), timeout=1)
        self.assertIn('查询超时', self.source.current_activity_payload()['error'])
        self.rpc.block_snapshot = False
        await self.source.refresh(force=True)
        self.assertTrue(self.source.current_activity_payload()['has_activity'])

    async def test_stream_and_admin_permissions_are_retained(self):
        await self.source.start()
        self.plugin.config.schedule.allowed_streams = ['session:allowed']
        result = await self.source.handle_tool('get_planning_status', stream_id='blocked')
        self.assertEqual(result['type'], 'error')
        self.plugin.config.schedule.admin_users = ['111']
        success, message, _ = await self.source.handle_command('status', user_id='222')
        self.assertFalse(success)
        self.assertIn('权限', message)

    async def test_list_image_escapes_external_text(self):
        await self.source.start()
        self.rpc.current['name'] = '<script>alert(1)</script>'
        success, _, _ = await self.source.handle_command('list', stream_id='session')
        self.assertTrue(success)
        render = next(call for call in self.rpc.calls if call['capability'] == 'render.html2png')
        self.assertNotIn('<script>', render['args']['html'])
        self.assertIn('&lt;script&gt;', render['args']['html'])

    async def test_runtime_uses_external_source_for_life_injection_and_reload(self):
        await start_runtime(self.plugin)
        self.assertIsInstance(self.plugin.schedule_engine, MaiLifeScheduleSource)
        self.assertEqual(self.plugin.life_engine.peek_state()['current_activity'], '图书馆自习')
        self.assertEqual(self.plugin.life_engine.peek_state()['current_location'], '图书馆')
        items = [{'item_type': 'UserMessageItem', 'parts': [{'type': 'text', 'text': '你现在在干什么？'}]}]
        injected = await self.plugin.handle_planner_life(session_id='session', items=items, item_schema_version=1)
        self.assertIn('图书馆自习', str(injected))
        data_dir = self.plugin.persistence.data_dir / 'schedule'
        self.assertFalse((data_dir / 'goals.db').exists())
        old = self.plugin.schedule_engine
        self.plugin.config.schedule.provider = 'internal'
        await self.plugin.on_config_update('self', {}, '6.3.0')
        self.assertFalse(old.started)
        self.assertIs(type(self.plugin.schedule_engine), ScheduleEngine)
        self.assertTrue((data_dir / 'goals.db').exists())
        self.plugin.config.schedule.provider = PROVIDER_ID
        await self.plugin.on_config_update('self', {}, '6.3.0')
        self.assertIsInstance(self.plugin.schedule_engine, MaiLifeScheduleSource)
        self.assertTrue((data_dir / 'goals.db').exists())

    async def test_late_provider_load_does_not_start_internal_generation(self):
        self.rpc.rpc_failure.add('get_schedule_provider_info')
        with self.assertLogs('plugin.Rabbit-Jia-Er.MaiTrace.mai_life', level='ERROR'):
            await start_runtime(self.plugin)
        self.assertTrue(self.plugin._ready)
        self.assertIsInstance(self.plugin.schedule_engine, MaiLifeScheduleSource)
        self.assertFalse((self.plugin.persistence.data_dir / 'schedule' / 'goals.db').exists())
        self.rpc.rpc_failure.clear()
        await self.plugin.schedule_engine.refresh(force=True)
        await self.plugin.life_engine.advance(self.plugin.schedule_engine.current_activity_payload())
        self.assertEqual(self.plugin.life_engine.peek_state()['current_activity'], '图书馆自习')
