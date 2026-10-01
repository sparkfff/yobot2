"""Performance scoring and saved weights use real records and Quart routes."""
import copy
from decimal import Decimal
from pathlib import Path
import sys
import tempfile
import unittest

from quart import Quart

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_battle_service as harness

from ybplugins.clan_battle.components import performance
from ybplugins.ybdata import Clan_challenge, Clan_group, Clan_member, User


class PerformanceTests(unittest.IsolatedAsyncioTestCase):
    group = harness.BattleServiceTests.group

    def setUp(self):
        harness.BattleServiceTests.setUp(self)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.battle.setting.update(dirname=self.tmp.name, public_basepath='/')
        Clan_group.update(threshold=40).where(Clan_group.group_id == 100).execute()
        self.app = Quart(__name__)
        self.app.secret_key = 'performance-test'
        self.app.config['TESTING'] = True
        self.battle.register_routes(self.app)
        self.client = self.app.test_client()
        self.api = '/clan/100/statistics/performance/api/'

    tearDown = harness.BattleServiceTests.tearDown

    def record(self, **changes):
        values = dict(gid=100, bid=0, qqid=10, challenge_pcrdate=harness.TODAY,
                      challenge_pcrtime=1, boss_cycle=1, boss_num=1,
                      boss_health_remain=80, challenge_damage=20, is_continue=False)
        values.update(changes)
        return Clan_challenge.create(**values)

    def report(self, bid=0):
        return performance.report(self.battle, self.group(), bid)

    async def login(self, qqid=1):
        async with self.client.session_transaction() as session:
            session.clear()
            if qqid is not None:
                session['yobot_user'] = qqid
                session['csrf_token'] = 'performance-csrf'

    async def put(self, data, **changes):
        body = dict(config=data['config'], revision=data['revision'], battle_id=0,
                    csrf_token='performance-csrf')
        body.update(changes)
        response = await self.client.put(self.api, json=body)
        return await response.get_json()

    def save(self, data, bid=0):
        performance.save_config(self.battle, self.group(), bid, data['config'],
                                data['revision'], performance.archive_records(100, bid))

    def test_default_points_threshold_and_behalf_credit(self):
        self.record(challenge_damage=1)  # A full knife always earns one point.
        self.record(boss_health_remain=0, challenge_damage=39)
        self.record(boss_health_remain=0, challenge_damage=40)
        self.record(is_continue=True, challenge_damage=39)
        self.record(is_continue=True, challenge_damage=40, behalf=30)
        result = self.report()
        rows = {row['qqid']: row for row in result['ranking']}
        self.assertEqual(rows[10]['score'], 3)
        self.assertEqual((rows[10]['full_blade'], rows[10]['end_blade'],
                          rows[10]['small_end_blade']), (1, 2, 1))
        self.assertEqual((rows[30]['score'], rows[30]['small_end_blade']), (1, 1))
        self.assertEqual(rows[20]['score'], 0)
        self.assertEqual(result['records'][-1]['credited_to'], 30)

    def test_stage_weight_and_single_record_override_replace(self):
        first = self.record()
        self.record(boss_cycle=4)
        data = self.report()
        data['config']['stages'][0]['weight'] = '2'
        data['config']['stages'][1]['weight'] = '3'
        data['config']['overrides'][str(first.cid)] = '0.25'
        self.save(data)
        result = self.report()
        self.assertEqual([r['score'] for r in result['records']], [.25, 3])
        self.assertEqual(result['ranking'][0]['score'], 3.25)

    def test_zero_override_survives_persistence_and_decimal_math(self):
        first = self.record()
        second = self.record(boss_health_remain=0, challenge_damage=1)
        data = self.report()
        data['config']['overrides'][str(second.cid)] = '0.3333'
        data['config']['overrides'][str(first.cid)] = '0'
        self.save(data)
        result = self.report()
        self.assertEqual(result['records'][0]['weight'], '0')
        self.assertEqual(result['ranking'][0]['score'], .16665)
        self.assertEqual(performance.weight('0'), Decimal(0))

    def test_bcd_stage_totals_include_override_and_behalf(self):
        self.battle.level_by_cycle['cn'] = [[1, 3], [4, 9], [10, 999]]
        self.record(boss_cycle=1)
        tail = self.record(boss_cycle=4, boss_health_remain=0, challenge_damage=1)
        self.record(boss_cycle=10, is_continue=True, challenge_damage=40, behalf=30)
        data = self.report()
        data['config']['stages'][1]['weight'] = '1.5'
        data['config']['stages'][2]['weight'] = '2.1'
        data['config']['overrides'][str(tail.cid)] = '0.3'
        self.save(data)
        rows = {row['qqid']: row for row in self.report()['ranking']}
        self.assertEqual(rows[10]['total_blades'], 2)
        self.assertEqual(rows[10]['stage_blades'], [1, 1, 0])
        self.assertEqual(rows[10]['stage_scores'], [1, .15, 0])
        self.assertEqual(rows[10]['score'], 1.15)
        self.assertEqual(rows[30]['stage_blades'], [0, 0, 1])
        self.assertEqual(rows[30]['stage_scores'], [0, 0, 2.1])
        data = self.report()
        data['config']['stages'][0]['weight'] = '1.25'
        with self.assertRaisesRegex(performance.PerformanceError, '1 位小数'):
            self.save(data)

    def test_group_command_renders_new_ranking_png_and_archive_month(self):
        import base64
        from datetime import datetime, timezone
        from io import BytesIO
        from PIL import Image
        from ybplugins.clan_battle.components.score import (
            performance_title, performance_cells, score_table)
        self.battle.level_by_cycle['cn'] = [[1, 3], [4, 9], [10, 999]]
        Clan_group.update(group_name='星光骑士团').where(Clan_group.group_id == 100).execute()
        self.battle.group_data_list.clear()
        day = int(datetime(2025, 9, 28, tzinfo=timezone.utc).timestamp() // 86400)
        self.record(challenge_pcrdate=day, boss_cycle=1)
        self.record(challenge_pcrdate=day + 1, boss_cycle=4)
        data = self.report()
        data['config']['stages'][1]['weight'] = '1.5'
        self.save(data)
        data = self.report()
        title = performance_title(self.group(), data)
        self.assertEqual(title, self.group().group_name + '-2025年09月-公会战业绩表')
        headers, rows, _ = performance_cells(data)
        self.assertEqual(headers[2:], ['总刀数', 'B阶段', 'C阶段', 'D阶段',
                                      'B得分', 'C得分', 'D得分', '总业绩分'])
        self.assertEqual(rows[0][2:], ['2', '1', '1', '0', '1', '1.5', '0', '2.5'])
        message = score_table(self.battle, 100)
        self.assertTrue(message.startswith('[CQ:image,file=base64://'))
        image = Image.open(BytesIO(base64.b64decode(message.split('base64://', 1)[1][:-1])))
        self.assertEqual(image.format, 'PNG')
        self.assertGreater(image.width, 1000)
        self.assertGreater(image.height, 150)

    def test_invalid_weights_and_ranges_do_not_replace_file(self):
        record = self.record()
        data = self.report()
        self.save(data)
        path = performance.config_path(self.battle, 100, 0)
        original = path.read_bytes()
        for value in ('NaN', 'Infinity', '-1', '100.1', '0.00001', True, None):
            with self.subTest(value=value):
                bad = self.report()
                bad['config']['overrides'][str(record.cid)] = value
                with self.assertRaises(performance.PerformanceError):
                    self.save(bad)
                self.assertEqual(path.read_bytes(), original)
        for stages in ([{'from': 2, 'to': 999, 'weight': 1}],
                       [{'from': 1, 'to': 3, 'weight': 1},
                        {'from': 3, 'to': 999, 'weight': 1}],
                       [{'from': 1, 'to': 0, 'weight': 1}]):
            bad = self.report()
            bad['config']['stages'] = stages
            with self.assertRaises(performance.PerformanceError):
                self.save(bad)
            self.assertEqual(path.read_bytes(), original)

    def test_foreign_group_and_archive_record_ids_rejected(self):
        self.record()
        for foreign in (self.record(gid=200), self.record(bid=1)):
            data = self.report()
            data['config']['overrides'][str(foreign.cid)] = '2'
            with self.assertRaises(performance.PerformanceError):
                self.save(data)
        self.assertFalse(performance.config_path(self.battle, 100, 0).exists())

    def test_fingerprint_prevents_reused_record_id_inheriting_weight(self):
        record = self.record()
        data = self.report()
        data['config']['overrides'][str(record.cid)] = '8'
        self.save(data)
        record.delete_instance()
        replacement = self.record(challenge_damage=30)
        self.assertEqual(record.cid, replacement.cid)  # SQLite reuses the highest deleted id.
        result = self.report()
        self.assertEqual(result['config']['overrides'], {})
        self.assertEqual(result['records'][0]['score'], 1)

    def test_removed_record_override_does_not_break_remaining_report(self):
        first, second = self.record(), self.record()
        data = self.report()
        data['config']['overrides'] = {str(first.cid): '2', str(second.cid): '3'}
        self.save(data)
        second.delete_instance()
        result = self.report()
        self.assertEqual(result['config']['overrides'], {str(first.cid): '2'})
        self.assertEqual(result['ranking'][0]['score'], 2)

    def test_saved_archive_stages_and_threshold_remain_stable(self):
        self.record(bid=1, boss_cycle=4, is_continue=True, challenge_damage=39)
        data = self.report(1)
        data['config']['stages'][1]['weight'] = '2'
        self.save(data, 1)
        self.battle.level_by_cycle['cn'] = [[1, 10], [11, 999]]
        Clan_group.update(threshold=1).where(Clan_group.group_id == 100).execute()
        result = self.report(1)
        self.assertEqual(result['records'][0]['stage'], 2)
        self.assertEqual(result['records'][0]['score'], 1)
        self.assertEqual(result['config']['threshold'], 40)

    async def test_member_readonly_and_other_clan_admin_cannot_save(self):
        self.record()
        for qqid in (10, 20):
            await self.login(qqid)
            response = await self.client.get(self.api)
            data = await response.get_json()
            self.assertEqual(data['code'], 0)
            self.assertFalse(data['can_edit'])
            self.assertEqual((await self.put(data))['code'], 11)
        self.assertFalse(performance.config_path(self.battle, 100, 0).exists())

    async def test_owner_and_membership_admin_can_save_with_csrf(self):
        self.record()
        Clan_member.update(role=10).where(Clan_member.group_id == 100,
                                         Clan_member.qqid == 10).execute()
        for qqid in (1, 10):
            await self.login(qqid)
            data = await (await self.client.get(self.api)).get_json()
            self.assertTrue(data['can_edit'])
            data['config']['stages'][0]['weight'] = str(qqid)
            result = await self.put(data)
            self.assertEqual(result['code'], 0)
            self.assertTrue(result['saved'])
            self.assertEqual(result['ranking'][0]['score'], qqid)

    async def test_missing_login_csrf_deleted_owner_denied(self):
        await self.login(None)
        self.assertEqual((await (await self.client.get(self.api)).get_json())['code'], 10)
        await self.login()
        data = self.report()
        self.assertEqual((await self.put(data, csrf_token=None))['code'], 15)
        async with self.client.session_transaction() as session:
            del session['csrf_token']
        self.assertEqual((await self.put(data))['code'], 15)
        User.update(deleted=True).where(User.qqid == 1).execute()
        self.assertEqual((await (await self.client.get(self.api)).get_json())['code'], 11)
        self.assertFalse(performance.config_path(self.battle, 100, 0).exists())

    async def test_stale_revision_cannot_overwrite_other_admin_change(self):
        self.record()
        await self.login()
        data = self.report()
        stale = copy.deepcopy(data)
        data['config']['stages'][0]['weight'] = '2'
        self.assertEqual((await self.put(data))['code'], 0)
        path = performance.config_path(self.battle, 100, 0)
        original = path.read_bytes()
        stale['config']['stages'][0]['weight'] = '3'
        self.assertEqual((await self.put(stale))['code'], 30)
        self.assertEqual(path.read_bytes(), original)

    async def test_archive_selection_and_invalid_request_bodies(self):
        self.record(bid=0)
        archived = self.record(bid=2, challenge_damage=33)
        await self.login()
        result = await (await self.client.get(self.api + '?battle_id=2')).get_json()
        self.assertEqual([record['cid'] for record in result['records']], [archived.cid])
        self.assertEqual(result['battle_id'], 2)
        for value in ('all', '-1', 'true', '1.5', '9999999999'):
            response = await self.client.get(self.api + '?battle_id=' + value)
            self.assertEqual((await response.get_json())['code'], 30)
        for body in ([], None, {'csrf_token': 'performance-csrf', 'battle_id': True}):
            response = await self.client.put(self.api, json=body)
            self.assertEqual((await response.get_json())['code'], 30)


if __name__ == '__main__':
    unittest.main()
