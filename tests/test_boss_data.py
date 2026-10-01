import copy
import datetime
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

from peewee import SqliteDatabase
from quart import Quart

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src' / 'client'))
from ybplugins.boss_data import BossDataError, fetch_boss_data, parse_boss_data
from ybplugins.settings import Setting
from ybplugins.ybdata import User


def fixture(year=2026, month=9):
    def phase(start, end, hp):
        return {'lapFrom': start, 'lapTo': end, 'bosses': [
            {'unitId': 999001 + n, 'name': f'Boss {n}', 'hp': hp + n}
            for n in range(5)]}
    return [{'year': year, 'month': month, 'phases': [
        phase(-2, -2, 10), phase(1, 6, 42000000),
        phase(7, 22, 70000000), phase(23, -1, 1320000000)]}]


class BossParserTests(unittest.TestCase):
    def test_filters_training_and_uses_official_health(self):
        result = parse_boss_data(fixture(), datetime.date(2026, 9, 1))
        self.assertEqual(result.cycles, [[1, 6], [7, 22], [23, 999]])
        self.assertEqual(result.health[0][0], 42000000)
        self.assertEqual(result.ids[0], '999001')

    def test_missing_current_month_requires_explicit_latest(self):
        with self.assertRaisesRegex(BossDataError, '2026-09'):
            parse_boss_data(fixture(), datetime.date(2026, 10, 2))
        result = parse_boss_data(fixture(), datetime.date(2026, 10, 2), True)
        self.assertEqual((result.year, result.month), (2026, 9))

    def test_sorts_periods_and_excludes_future_data(self):
        data = fixture(2026, 9) + fixture(2026, 11) + fixture(2026, 8)
        self.assertEqual(parse_boss_data(data, datetime.date(2026, 10, 2), True).month, 9)

    def test_accepts_numeric_strings(self):
        data = fixture()
        data[0]['year'], data[0]['month'] = '2026', '9'
        self.assertEqual(parse_boss_data(data, datetime.date(2026, 9, 2)).year, 2026)

    def test_invalid_response_is_rejected_without_mutation(self):
        for data in [None, {}, [], [{}]]:
            with self.subTest(data=data), self.assertRaises(BossDataError):
                parse_boss_data(data)
        for change in ['hp', 'count', 'start', 'range', 'name', 'boolean']:
            data = fixture()
            phase = data[0]['phases'][1]
            if change == 'hp': phase['bosses'][0]['hp'] = 0
            if change == 'count': phase['bosses'].pop()
            if change == 'start': phase['lapFrom'] = 2
            if change == 'range': phase['lapTo'] = 10
            if change == 'name': phase['bosses'][0]['name'] = None
            if change == 'boolean': phase['bosses'][0]['hp'] = True
            original = copy.deepcopy(data)
            with self.subTest(change=change), self.assertRaises(BossDataError):
                parse_boss_data(data, datetime.date(2026, 9, 2))
            self.assertEqual(data, original)


class BossFetchTests(unittest.IsolatedAsyncioTestCase):
    async def test_http_status_checked_before_json(self):
        response = Mock()
        response.raise_for_status.side_effect = OSError('HTTP 503')
        response.json = AsyncMock()
        context = AsyncMock()
        context.__aenter__.return_value = response
        client = Mock()
        client.get.return_value = context
        with self.assertRaisesRegex(OSError, '503'):
            await fetch_boss_data(client, 'cn')
        response.json.assert_not_awaited()


class BossUpdateRoutesTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = SqliteDatabase(':memory:')
        self.binding = self.db.bind_ctx([User])
        self.binding.__enter__()
        self.db.connect()
        self.db.create_tables([User])
        User.create(qqid=1, authority_group=1)
        self.setting = {'dirname': self.tmp.name, 'verinfo': {}, 'public_basepath': '/',
                        'boss': {'cn': [[1] * 5], 'jp': [[2] * 5], 'tw': [[3] * 5]},
                        'boss_id': {s: ['1'] * 5 for s in ('cn', 'jp', 'tw')},
                        'level_by_cycle': {s: [[1, 999]] for s in ('cn', 'jp', 'tw')}}
        self.names = {'1': {'1': 'Old'}}
        self.app = Quart(__name__)
        self.app.secret_key = 'test'
        Setting(self.setting, None, self.names).register_routes(self.app)
        self.client = self.app.test_client()

    def tearDown(self):
        self.db.close()
        self.binding.__exit__(None, None, None)
        self.tmp.cleanup()

    async def update(self, results, use_latest=False, icon_error=None, save_error=None):
        async with self.client.session_transaction() as session:
            session['yobot_user'], session['csrf_token'] = 1, 'test'
        context = AsyncMock()
        with patch('ybplugins.settings.create_session', return_value=context), \
                patch('ybplugins.settings.fetch_boss_data', new=AsyncMock(side_effect=results)) as fetch, \
                patch('ybplugins.settings.download_icons', new=AsyncMock(side_effect=icon_error)):
            if save_error:
                with patch('ybplugins.settings.save_json', side_effect=save_error):
                    response = await self.client.post('/admin/setting/auto_get_boss_data/',
                        json={'csrf_token': 'test', 'use_latest': use_latest})
            else:
                response = await self.client.post('/admin/setting/auto_get_boss_data/',
                    json={'csrf_token': 'test', 'use_latest': use_latest})
            return await response.get_json(), fetch

    async def test_all_failure_does_not_save_or_mutate(self):
        original = copy.deepcopy(self.setting)
        result, _ = await self.update([BossDataError('missing'), TimeoutError()])
        self.assertEqual(result['code'], 32)
        self.assertEqual(self.setting, original)
        self.assertEqual(list(Path(self.tmp.name).iterdir()), [])

    async def test_partial_update_preserves_other_servers_and_shared_objects(self):
        data = parse_boss_data(fixture(), datetime.date(2026, 9, 2))
        shared = self.setting['boss']
        result, fetch = await self.update([data, BossDataError('missing')], True)
        self.assertEqual(result['code'], 0)
        self.assertTrue(result['partial'])
        self.assertIn('2026-09', result['message'])
        self.assertIs(self.setting['boss'], shared)
        self.assertEqual(shared['cn'][0][0], 42000000)
        self.assertEqual(shared['jp'], [[2] * 5])
        self.assertEqual(shared['tw'], [[3] * 5])
        self.assertTrue(fetch.call_args_list[0].args[2])
        saved = json.loads((Path(self.tmp.name) / 'yobot_config.json').read_text(encoding='utf-8'))
        self.assertEqual(saved['boss'], shared)
        self.assertNotIn('dirname', saved)
        self.assertEqual(self.names['1']['1'], 'Old')
        self.assertEqual(self.names['1']['999001'], 'Boss 0')

    async def test_icon_failure_keeps_successfully_saved_data(self):
        data = parse_boss_data(fixture(), datetime.date(2026, 9, 2))
        result, _ = await self.update([data, data], icon_error=OSError('icons down'))
        self.assertEqual(result['code'], 0)
        self.assertFalse(result['partial'])
        self.assertIn('头像', result['message'])
        self.assertTrue((Path(self.tmp.name) / 'yobot_config.json').exists())

    async def test_save_failure_does_not_mutate_runtime(self):
        data = parse_boss_data(fixture(), datetime.date(2026, 9, 2))
        original, names = copy.deepcopy(self.setting), copy.deepcopy(self.names)
        result, _ = await self.update([data, data], save_error=OSError('disk full'))
        self.assertEqual(result['code'], 31)
        self.assertEqual(self.setting, original)
        self.assertEqual(self.names, names)
