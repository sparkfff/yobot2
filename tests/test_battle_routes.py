"""Real Quart adapters must return committed battle state and enforce clan roles."""
import json
import sys
import unittest
from pathlib import Path

from quart import Quart

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_battle_service as harness

from ybplugins.ybdata import Clan_challenge, Clan_group, Clan_member


class BattleRouteTests(unittest.IsolatedAsyncioTestCase):
    # Reuse setup helpers without inheriting and collecting the service tests.
    tearDown = harness.BattleServiceTests.tearDown
    group = harness.BattleServiceTests.group

    def setUp(self):
        harness.BattleServiceTests.setUp(self)
        self.battle.setting.update(public_basepath='/', boss=self.battle.bossinfo)
        self.app = Quart(__name__)
        self.app.secret_key = 'battle-route-tests'
        self.app.config['TESTING'] = True
        self.battle.register_routes(self.app)
        self.client = self.app.test_client()

    async def login(self, qqid):
        async with self.client.session_transaction() as sess:
            sess.clear()
            sess['yobot_user'] = qqid
            sess['csrf_token'] = 'route-csrf'

    async def call(self, payload, *, setting=False):
        body = dict(payload, csrf_token='route-csrf')
        path = '/clan/100/setting/api/' if setting else '/clan/100/api/'
        response = await self.client.post(path, json=body)
        self.assertEqual(response.status_code, 200)
        return await response.get_json()

    async def report(self, damage=25):
        return await self.call({
            'action': 'addrecord', 'defeat': False, 'damage': damage,
            'behalf': None, 'is_continue': False, 'boss_num': 1,
        })

    async def test_addrecord_and_undo_responses_match_committed_health(self):
        await self.login(10)
        result = await self.report()
        self.assertEqual(result['code'], 0)
        self.assertEqual(result['bossData']['1']['health'], 75)
        self.assertEqual(result['bossData']['1']['health'],
                         json.loads(self.group().now_cycle_boss_health)['1'])
        self.assertEqual(Clan_challenge.select().count(), 1)
        result = await self.call({'action': 'undo'})
        self.assertEqual(result['code'], 0)
        self.assertEqual(result['bossData']['1']['health'], 100)
        self.assertEqual(result['bossData']['1']['health'],
                         json.loads(self.group().now_cycle_boss_health)['1'])
        self.assertEqual(Clan_challenge.select().count(), 0)

    async def test_addrecord_refreshes_existing_stale_cached_model(self):
        await self.login(10)
        cached = self.battle.get_clan_group(100)
        updated = json.loads(self.initial_health)
        updated['1'] = 80
        Clan_group.update(now_cycle_boss_health=json.dumps(updated)).where(
            Clan_group.group_id == 100).execute()
        result = await self.report(damage=20)
        self.assertEqual(result['code'], 0)
        self.assertEqual(result['bossData']['1']['health'], 60)
        self.assertEqual(json.loads(self.group().now_cycle_boss_health)['1'], 60)
        self.assertIs(self.battle.get_clan_group(100), cached)

    async def test_failed_report_preserves_database_cache_and_update_future(self):
        await self.login(10)
        cached = self.battle.get_clan_group(100)
        future = self.battle._boss_status[100]
        result = await self.report(damage=100)
        self.assertEqual(result['code'], 10)
        self.assertEqual(self.group().now_cycle_boss_health, self.initial_health)
        self.assertEqual(cached.now_cycle_boss_health, self.initial_health)
        self.assertEqual(Clan_challenge.select().count(), 0)
        self.assertFalse(future.done())

    async def test_other_clan_admin_cannot_modify_or_clear_current_clan(self):
        await self.login(10)
        await self.report()
        original_health = self.group().now_cycle_boss_health
        await self.login(20)  # Global legacy role 10, current membership role 100.
        result = await self.call({
            'action': 'modify', 'cycle': 1,
            'bossData': {'1': {'is_next': False, 'health': 50}},
        })
        self.assertEqual(result['code'], 11)
        result = await self.call({'action': 'clear_data_slot', 'battle_id': 0}, setting=True)
        self.assertEqual(result['code'], 11)
        self.assertEqual(self.group().now_cycle_boss_health, original_health)
        self.assertEqual(Clan_challenge.select().count(), 1)

    async def test_current_clan_membership_admin_can_modify_and_clear(self):
        await self.login(10)
        await self.report()
        Clan_member.update(role=10).where(
            Clan_member.group_id == 100, Clan_member.qqid == 10).execute()
        result = await self.call({
            'action': 'modify', 'cycle': 1,
            'bossData': {'1': {'is_next': False, 'health': 50}},
        })
        self.assertEqual(result['code'], 0)
        self.assertEqual(result['bossData']['1']['health'], 50)
        self.assertEqual(json.loads(self.group().now_cycle_boss_health)['1'], 50)
        result = await self.call({'action': 'clear_data_slot', 'battle_id': 0}, setting=True)
        self.assertEqual(result['code'], 0)
        self.assertEqual(self.group().now_cycle_boss_health, self.initial_health)
        self.assertEqual(Clan_challenge.select().count(), 0)


if __name__ == '__main__':
    unittest.main()
