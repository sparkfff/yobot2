"""Regression tests use real SQLite models and the public ClanBattle methods."""
import asyncio
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import AsyncMock, patch

from peewee import SqliteDatabase

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src' / 'client'))

from ybplugins.ybdata import Clan_challenge, Clan_challenge_undo, Clan_group, Clan_group_backups, Clan_member, User
from ybplugins.clan_battle.battle import ClanBattle
from ybplugins.clan_battle.exception import GroupError, InputError, UserError
from ybplugins.clan_battle.components.battle_state import after_commit

MODELS = [User, Clan_group, Clan_member, Clan_challenge, Clan_group_backups, Clan_challenge_undo]
TODAY = 20000


class BattleServiceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.db = SqliteDatabase(':memory:')
        self.binding = self.db.bind_ctx(MODELS)
        self.binding.__enter__()
        self.db.connect()
        self.db.create_tables(MODELS)
        for qqid, authority in [(1, 1), (10, 100), (20, 10), (30, 100)]:
            User.create(qqid=qqid, nickname=str(qqid), authority_group=authority)
            Clan_member.create(group_id=100, qqid=qqid, role=100)
        Clan_member.create(group_id=200, qqid=20, role=10)
        self.battle = ClanBattle.__new__(ClanBattle)
        self.battle.group_data_list = {}
        self.battle._boss_status = {100: asyncio.get_event_loop().create_future()}
        self.battle.api = AsyncMock()
        self.battle.bossinfo = {'cn': [[100] * 5, [200] * 5]}
        self.battle.level_by_cycle = {'cn': [[1, 3], [4, 999]]}
        self.battle.setting = {'boss_id': {'cn': ['1'] * 5}}
        self.battle.boss_id_name = {str(number): {'1': 'Boss'} for number in range(1, 6)}
        self.initial_health = json.dumps({str(number): 100 for number in range(1, 6)})
        Clan_group.create(group_id=100, game_server='cn', notification=0,
                          now_cycle_boss_health=self.initial_health,
                          next_cycle_boss_health=self.initial_health)
        self.clock = patch('ybplugins.clan_battle.components.battle_service.pcr_datetime', return_value=(TODAY, 123))
        self.clock.start()
        self.addCleanup(self.clock.stop)
        self.application_clock = patch('ybplugins.clan_battle.components.realize.pcr_datetime', return_value=(TODAY, 123))
        self.application_clock.start()
        self.addCleanup(self.application_clock.stop)
        self.bot_mapping = patch('ybplugins.clan_battle.components.realize.who_am_i', return_value=999)
        self.bot_mapping.start()
        self.addCleanup(self.bot_mapping.stop)

    def tearDown(self):
        self.db.close()
        self.binding.__exit__(None, None, None)

    def group(self):
        return Clan_group.get_by_id(100)

    def record(self, qqid=10, date=TODAY, health=80, compensation=False, bid=0):
        return Clan_challenge.create(gid=100, bid=bid, qqid=qqid,
                                     challenge_pcrdate=date, challenge_pcrtime=1,
                                     boss_cycle=1, boss_num=1, boss_health_remain=health,
                                     challenge_damage=20, is_continue=compensation)

    def application(self, boss='1', qqid='10', tree=True):
        state = {boss: {qqid: {'is_continue': False, 'behalf': None, 's': 2,
                              'damage': 40, 'tree': tree, 'msg': 'preserve me'}}}
        Clan_group.update(challenging_member_list=json.dumps(state)).where(Clan_group.group_id == 100).execute()
        return json.dumps(state)

    async def test_rejected_explicit_report_preserves_tree_damage_and_notes(self):
        original = self.application()
        future = self.battle._boss_status[100]
        with self.assertRaises(InputError):
            self.battle.challenge(100, 10, False, 100, boss_num=1)
        self.assertEqual(self.group().challenging_member_list, original)
        self.assertEqual(self.group().now_cycle_boss_health, self.initial_health)
        self.assertEqual(Clan_challenge.select().count(), 0)
        self.assertFalse(future.done())

    async def test_failed_report_without_application_does_not_create_one(self):
        with self.assertRaises(InputError):
            self.battle.challenge(100, 10, False, 200, boss_num=1)
        self.assertIsNone(self.group().challenging_member_list)

    async def test_normal_report_updates_record_health_and_only_own_reservation(self):
        self.application()
        Clan_group.update(subscribe_list=json.dumps({'1': {'10': 'mine', '30': 'other'}})).where(Clan_group.group_id == 100).execute()
        future = self.battle._boss_status[100]
        self.battle.challenge(100, 10, False, 25)
        report = Clan_challenge.get()
        self.assertEqual((report.challenge_damage, report.boss_health_remain), (25, 75))
        self.assertEqual(json.loads(self.group().now_cycle_boss_health)['1'], 75)
        self.assertIsNone(self.group().challenging_member_list)
        self.assertEqual(json.loads(self.group().subscribe_list), {'1': {'30': 'other'}})
        self.assertTrue(future.done())

    async def test_exception_after_writes_rolls_back_records_cache_and_notifications(self):
        original = self.application()
        effect_log = []
        def enqueue(*args):
            after_commit(lambda: effect_log.append('sent'))
        with patch('ybplugins.clan_battle.components.battle_service.send_group_notification', side_effect=enqueue), \
                patch.object(self.battle, 'challenger_info_small', side_effect=RuntimeError('injected failure')):
            with self.assertRaisesRegex(RuntimeError, 'injected failure'):
                self.battle.challenge(100, 10, True, boss_num=1)
        self.assertEqual(Clan_challenge.select().count(), 0)
        self.assertEqual(self.group().challenging_member_list, original)
        self.assertEqual(self.battle.get_clan_group(100).now_cycle_boss_health, self.initial_health)
        self.assertEqual(effect_log, [])
        self.assertFalse(self.battle._boss_status[100].done())

    async def test_notification_runs_after_commit(self):
        self.application()
        observed = []
        def enqueue(*args):
            after_commit(lambda: observed.append((self.db.transaction_depth(), Clan_challenge.select().count())))
        with patch('ybplugins.clan_battle.components.battle_service.send_group_notification', side_effect=enqueue):
            self.battle.challenge(100, 10, True, boss_num=1)
        self.assertEqual(observed, [(0, 1)])

    async def test_notification_failure_does_not_fail_a_saved_report(self):
        self.application()
        def enqueue(*args):
            after_commit(lambda: (_ for _ in ()).throw(RuntimeError('notification failure')))
        with patch('ybplugins.clan_battle.components.battle_service.send_group_notification', side_effect=enqueue), \
                self.assertLogs('ybplugins.clan_battle.components.battle_state', level='ERROR'):
            result = self.battle.challenge(100, 10, True, boss_num=1)
        self.assertIn('击败了boss', result)
        self.assertEqual(Clan_challenge.select().count(), 1)

    async def test_full_tail_generates_compensation_and_report_consumes_it(self):
        self.battle.challenge(100, 10, True, boss_num=1)
        self.battle.challenge(100, 10, False, 20, is_continue=True, boss_num=2)
        reports = list(Clan_challenge.select().order_by(Clan_challenge.cid))
        self.assertEqual([r.is_continue for r in reports], [False, True])
        with self.assertRaises(GroupError):
            self.battle.challenge(100, 10, False, 20, is_continue=True, boss_num=3)

    async def test_three_full_blades_selects_available_compensation_automatically(self):
        self.record(health=80)
        self.record(health=80)
        self.record(health=0)
        self.battle.challenge(100, 10, False, 10, boss_num=1)
        self.assertTrue(Clan_challenge.select().order_by(Clan_challenge.cid.desc()).get().is_continue)

    async def test_daily_limit_rejects_without_mutating_existing_application(self):
        original = self.application()
        for _ in range(3):
            self.record()
        with self.assertRaises(InputError):
            self.battle.challenge(100, 10, False, 10, boss_num=2)
        self.assertEqual(self.group().challenging_member_list, original)
        self.assertEqual(Clan_challenge.select().count(), 3)

    async def test_previous_day_counts_use_previous_day_compensation(self):
        self.record(date=TODAY - 1, health=0)
        self.battle.challenge(100, 10, False, 10, is_continue=True, boss_num=1, previous_day=True)
        report = Clan_challenge.select().order_by(Clan_challenge.cid.desc()).get()
        self.assertEqual(report.challenge_pcrdate, TODAY - 1)
        self.assertTrue(report.is_continue)

    async def test_rollover_preserves_next_cycle_progress_and_undo_restores_it(self):
        now = {str(number): 0 for number in range(1, 6)}
        now['5'] = 40
        next_cycle = {str(number): 100 for number in range(1, 6)}
        next_cycle['1'] = 60
        Clan_group.update(now_cycle_boss_health=json.dumps(now), next_cycle_boss_health=json.dumps(next_cycle)).where(Clan_group.group_id == 100).execute()
        self.battle.challenge(100, 10, True, boss_num=5)
        self.assertEqual(self.group().boss_cycle, 2)
        self.assertEqual(json.loads(self.group().now_cycle_boss_health), next_cycle)
        self.battle.undo(100, 10)
        self.assertEqual(self.group().boss_cycle, 1)
        self.assertEqual(json.loads(self.group().now_cycle_boss_health), now)
        self.assertEqual(json.loads(self.group().next_cycle_boss_health), next_cycle)

    async def test_another_clan_administrator_cannot_undo_other_member(self):
        self.record(qqid=10)
        with self.assertRaises(UserError):
            self.battle.undo(100, 20)
        self.assertEqual(Clan_challenge.select().count(), 1)
        Clan_member.update(role=10).where(Clan_member.group_id == 100, Clan_member.qqid == 20).execute()
        self.battle.undo(100, 20)
        self.assertEqual(Clan_challenge.select().count(), 0)

    async def test_clearing_historical_archive_preserves_live_progress(self):
        original = self.application()
        self.record(bid=0)
        self.record(bid=2)
        Clan_group_backups.create(group_id=100, battle_id=2, group_data='stale')
        self.battle.clear_data_slot(100, 2)
        self.assertEqual(self.group().challenging_member_list, original)
        self.assertEqual(Clan_challenge.select().get().bid, 0)
        self.assertEqual(Clan_group_backups.select().count(), 0)
        self.assertFalse(self.battle._boss_status[100].done())

    async def test_archive_roundtrip_and_same_archive_switch(self):
        self.battle.challenge(100, 10, False, 25, boss_num=1)
        self.battle.switch_data_slot(100, 2)
        self.assertEqual(self.group().now_cycle_boss_health, self.initial_health)
        self.battle.switch_data_slot(100, 0)
        self.assertEqual(json.loads(self.group().now_cycle_boss_health)['1'], 75)
        self.battle.switch_data_slot(100, 0)
        self.assertEqual(json.loads(self.group().now_cycle_boss_health)['1'], 75)

    async def test_clear_current_archive_resets_progress_and_records(self):
        self.battle.challenge(100, 10, False, 25, boss_num=1)
        self.battle.clear_data_slot(100)
        self.assertEqual(self.group().boss_cycle, 1)
        self.assertEqual(self.group().now_cycle_boss_health, self.initial_health)
        self.assertEqual(Clan_challenge.select().count(), 0)

    async def test_invalid_boss_and_archive_identifiers_are_rejected(self):
        for value in (0, 6, True, '../1'):
            with self.assertRaises(InputError):
                self.battle.challenge(100, 10, False, 10, boss_num=value)
        for value in (-1, True, '2'):
            with self.assertRaises(InputError):
                self.battle.clear_data_slot(100, value)
        self.assertEqual(Clan_challenge.select().count(), 0)

    async def test_operation_reloads_database_instead_of_using_stale_cache(self):
        self.battle.get_clan_group(100)
        health = json.loads(self.initial_health)
        health['1'] = 50
        Clan_group.update(now_cycle_boss_health=json.dumps(health)).where(Clan_group.group_id == 100).execute()
        self.battle.challenge(100, 10, False, 10, boss_num=1)
        self.assertEqual(json.loads(self.group().now_cycle_boss_health)['1'], 40)

    async def test_application_reloads_health_and_accepts_integer_boss_number(self):
        cached = self.battle.get_clan_group(100)
        health = json.loads(self.initial_health)
        health['1'] = 50
        Clan_group.update(now_cycle_boss_health=json.dumps(health)).where(Clan_group.group_id == 100).execute()
        self.battle.apply_for_challenge(False, 100, 10, 1)
        self.assertEqual(json.loads(self.group().now_cycle_boss_health)['1'], 50)
        self.assertIs(self.battle.get_clan_group(100), cached)
        self.assertEqual(json.loads(cached.now_cycle_boss_health)['1'], 50)

    async def test_external_transaction_is_rejected_before_writes_or_updates(self):
        with self.db.atomic():
            with self.assertRaisesRegex(RuntimeError, 'outer transaction'):
                self.battle.challenge(100, 10, False, 10, boss_num=1)
            self.assertEqual(Clan_challenge.select().count(), 0)
        self.assertFalse(self.battle._boss_status[100].done())

    async def test_nested_application_failure_rolls_back_cached_state(self):
        with patch.object(self.battle, 'challenger_info_small', side_effect=RuntimeError('injected failure')):
            with self.assertRaisesRegex(GroupError, 'injected failure'):
                self.battle.put_on_the_tree(100, 10, boss_num=1)
        self.assertIsNone(self.group().challenging_member_list)
        self.assertIsNone(self.battle.get_clan_group(100).challenging_member_list)
        self.assertFalse(self.battle._boss_status[100].done())
