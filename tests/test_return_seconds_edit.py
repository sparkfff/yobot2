"""Real Quart return-second edits enforce ownership, scope and stale protection."""
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_battle_routes as routes
import test_battle_service as harness
from ybplugins.ybdata import Clan_challenge, Clan_group, Clan_member


class ReturnSecondsEditTests(unittest.IsolatedAsyncioTestCase):
    setUp = routes.BattleRouteTests.setUp
    tearDown = routes.BattleRouteTests.tearDown
    login = routes.BattleRouteTests.login
    call = routes.BattleRouteTests.call
    group = harness.BattleServiceTests.group

    def record(self, qqid=10, health=0, compensation=False, gid=100, bid=0, cycle=1):
        return Clan_challenge.create(gid=gid, bid=bid, qqid=qqid,
            challenge_pcrdate=harness.TODAY, challenge_pcrtime=1, boss_cycle=cycle,
            boss_num=1, boss_health_remain=health, challenge_damage=20,
            is_continue=compensation)

    async def edit(self, record, seconds=41, **extra):
        return await self.call(dict(action='set_return_seconds', record_id=record.cid,
                                    return_seconds=seconds, **extra))

    async def report(self):
        with patch('ybplugins.clan_battle.components.web_operation.pcr_datetime',
                   return_value=(harness.TODAY, 1)):
            return (await self.call(dict(action='get_challenge', ts=None)))['challenges']

    async def test_owner_edits_null_and_cache_refreshes_for_other_viewers(self):
        self.battle.level_by_cycle = {'cn': [[1, 3], [4, 6], [7, 999]]}
        record = self.record(cycle=7)
        health = self.group().now_cycle_boss_health
        await self.login(10)
        before = (await self.report())[0]
        self.assertTrue(before['can_edit_return_seconds'])
        self.assertIsNone(before['return_seconds'])
        self.battle.get_report(100, None, 10, None, nocache=True)
        result = await self.edit(record, expected_return_seconds=None)
        self.assertEqual(result['code'], 0)
        self.assertEqual(result['recorded_return_seconds'], 41)
        await self.login(30)
        after = (await self.report())[0]
        self.assertEqual(after['return_seconds'], 41)
        self.assertFalse(after['can_edit_return_seconds'])
        user_report = await self.call(dict(action='get_user_challenge', qqid=10))
        self.assertEqual(user_report['challenges'][0]['recorded_return_seconds'], 41)
        self.assertEqual((await self.edit(record))['code'], 11)
        await self.login(10)
        self.assertTrue((await self.report())[0]['can_edit_return_seconds'])
        self.assertEqual(Clan_challenge.get_by_id(record.cid).return_seconds, 41)
        self.assertEqual(Clan_challenge.select().count(), 1)
        self.assertEqual(self.group().now_cycle_boss_health, health)
        from ybplugins.clan_battle.components import realize
        with patch.object(realize, 'get_process_image', wraps=realize.get_process_image) as render:
            self.battle.challenger_info(100)
        self.assertIn('41s', render.call_args.args[1]['补偿']['10'])

    async def test_only_current_clan_or_global_admin_can_edit_others(self):
        record = self.record(qqid=30)
        await self.login(20)  # Administrator of a different clan.
        self.assertEqual((await self.edit(record))['code'], 11)
        Clan_member.update(role=10).where(Clan_member.group_id == 100,
                                         Clan_member.qqid == 20).execute()
        self.assertEqual((await self.edit(record, 21))['code'], 0)
        await self.login(1)
        self.assertEqual((await self.edit(record, 90))['code'], 0)

    async def test_csrf_guest_and_invalid_values_never_write(self):
        record = self.record()
        await self.login(10)
        response = await self.client.post('/clan/100/api/', json=dict(
            action='set_return_seconds', record_id=record.cid, return_seconds=41))
        self.assertEqual((await response.get_json())['code'], 15)
        for seconds in (-1, 0, 20, 91, True, '41', 1.5, None):
            self.assertEqual((await self.edit(record, seconds))['code'], 30)
        for cid in (True, '1', None, -1):
            self.assertEqual((await self.call(dict(action='set_return_seconds',
                record_id=cid, return_seconds=41)))['code'], 30)
        Clan_group.update(privacy=1).where(Clan_group.group_id == 100).execute()
        self.battle.group_data_list.clear()
        async with self.client.session_transaction() as sess:
            sess.clear()
        self.assertFalse((await self.report())[0]['can_edit_return_seconds'])
        self.assertEqual((await self.edit(record))['code'], 10)
        self.assertIsNone(Clan_challenge.get_by_id(record.cid).return_seconds)

    async def test_scope_consumed_deleted_and_optimistic_conflicts(self):
        await self.login(10)
        for record in (self.record(gid=200), self.record(bid=1),
                       self.record(health=80), self.record(compensation=True)):
            self.assertEqual((await self.edit(record))['code'], 30)
        consumed = self.record()
        self.record(compensation=True)
        self.assertEqual((await self.edit(consumed))['code'], 30)
        pending = self.record()
        self.assertEqual((await self.edit(pending, expected_return_seconds=90))['code'], 30)
        self.assertEqual((await self.edit(pending, expected_return_seconds=False))['code'], 30)
        self.assertEqual((await self.edit(pending, expected_return_seconds=None))['code'], 0)
        self.assertEqual((await self.edit(pending, expected_return_seconds=None))['code'], 30)
        pending.delete_instance()
        self.assertEqual((await self.edit(pending))['code'], 30)
