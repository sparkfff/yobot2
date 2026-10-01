"""Complete undo restores persisted report state without erasing later edits."""
import asyncio
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

from peewee import SqliteDatabase

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_battle_service as harness

from ybplugins.ybdata import Clan_challenge, Clan_challenge_undo, Clan_group
from ybplugins.clan_battle.battle import ClanBattle
from ybplugins.clan_battle.exception import GroupError
from ybplugins.clan_battle.components.report_undo import BattleSnapshot, UndoConflict
from ybplugins.clan_battle.components.battle_state import count_blades


class CompleteUndoTests(unittest.IsolatedAsyncioTestCase):
    setUp = harness.BattleServiceTests.setUp
    tearDown = harness.BattleServiceTests.tearDown
    group = harness.BattleServiceTests.group
    record = harness.BattleServiceTests.record
    application = harness.BattleServiceTests.application

    def reserve(self, reservations):
        Clan_group.update(subscribe_list=json.dumps(reservations)).where(Clan_group.group_id == 100).execute()

    def snapshot(self):
        return BattleSnapshot.capture(self.group())

    async def test_report_undo_restores_application_tree_damage_notes_and_reservation(self):
        self.application()
        self.reserve({'1': {'10': 'original note', '30': 'other note'}})
        original = self.snapshot()
        self.battle.challenge(100, 10, False, 25)
        self.assertEqual(Clan_challenge_undo.select().count(), 1)
        self.battle.undo(100, 10)
        self.assertEqual(self.snapshot(), original)
        self.assertEqual(Clan_challenge.select().count(), 0)
        self.assertEqual(Clan_challenge_undo.select().count(), 0)

    async def test_tail_undo_restores_all_members_on_the_boss(self):
        self.application()
        self.battle.apply_for_challenge(False, 100, 30, '1')
        self.battle.put_on_the_tree(100, 30, message='other tree')
        original = self.snapshot()
        self.battle.challenge(100, 10, True, boss_num=1)
        self.assertIsNone(self.group().challenging_member_list)
        self.battle.undo(100, 10)
        self.assertEqual(self.snapshot(), original)

    async def test_later_other_members_applications_and_reservations_survive(self):
        self.application()
        self.reserve({'1': {'10': 'restore me'}})
        self.battle.challenge(100, 10, False, 25)
        self.battle.apply_for_challenge(False, 100, 20, '1')
        self.battle.report_hurt(3, 15, 100, 20)
        self.reserve({'1': {'20': 'new reservation'}, '2': {'30': 'another boss'}})
        self.battle.undo(100, 10)
        applications = json.loads(self.group().challenging_member_list)
        self.assertEqual(applications['1']['10']['msg'], 'preserve me')
        self.assertEqual(applications['1']['20']['damage'], 15)
        self.assertEqual(json.loads(self.group().subscribe_list), {
            '1': {'10': 'restore me', '20': 'new reservation'}, '2': {'30': 'another boss'}})

    async def test_member_reapplying_to_another_boss_rejects_double_application(self):
        self.application()
        self.battle.challenge(100, 10, False, 25)
        self.battle.apply_for_challenge(False, 100, 10, '2')
        current = self.snapshot()
        future = self.battle._boss_status[100]
        with self.assertRaises(UndoConflict):
            self.battle.undo(100, 10)
        self.assertEqual(self.snapshot(), current)
        self.assertEqual(Clan_challenge.select().count(), 1)
        self.assertEqual(Clan_challenge_undo.select().count(), 1)
        self.assertFalse(future.done())

    async def test_member_reapplying_to_same_boss_rejects_overwriting_new_state(self):
        self.application()
        self.battle.challenge(100, 10, False, 25)
        self.battle.apply_for_challenge(False, 100, 10, '1')
        self.battle.report_hurt(4, 60, 100, 10)
        current = self.snapshot()
        with self.assertRaises(UndoConflict):
            self.battle.undo(100, 10)
        self.assertEqual(self.snapshot(), current)
        self.assertEqual(Clan_challenge.select().count(), 1)

    async def test_changed_health_rejects_undo_without_deleting_record_or_snapshot(self):
        self.battle.challenge(100, 10, False, 25, boss_num=1)
        health = json.loads(self.group().now_cycle_boss_health)
        health['1'] = 50
        Clan_group.update(now_cycle_boss_health=json.dumps(health)).where(Clan_group.group_id == 100).execute()
        current = self.snapshot()
        with self.assertRaises(UndoConflict):
            self.battle.undo(100, 10)
        self.assertEqual(self.snapshot(), current)
        self.assertEqual(Clan_challenge.select().count(), 1)
        self.assertEqual(Clan_challenge_undo.select().count(), 1)

    async def test_changed_reservation_rejects_undo_without_losing_new_note(self):
        self.reserve({'1': {'10': 'old reservation'}})
        self.battle.challenge(100, 10, False, 25, boss_num=1)
        self.reserve({'1': {'10': 'new reservation'}})
        current = self.snapshot()
        with self.assertRaises(UndoConflict):
            self.battle.undo(100, 10)
        self.assertEqual(self.snapshot(), current)

    async def test_changed_server_rejects_snapshot_undo(self):
        self.battle.challenge(100, 10, False, 25, boss_num=1)
        Clan_group.update(game_server='jp').where(Clan_group.group_id == 100).execute()
        with self.assertRaises(UndoConflict):
            self.battle.undo(100, 10)
        self.assertEqual(Clan_challenge.select().count(), 1)

    async def test_snapshot_survives_database_reopen_and_new_service_instance(self):
        self.application()
        self.reserve({'1': {'10': 'durable reservation'}})
        original = self.snapshot()
        self.battle.challenge(100, 10, False, 25)
        with tempfile.TemporaryDirectory() as directory:
            filename = str(Path(directory) / 'restart.sqlite')
            copy = sqlite3.connect(filename)
            try:
                self.db.connection().backup(copy)
            finally:
                copy.close()
            database = SqliteDatabase(filename)
            with database.bind_ctx(harness.MODELS):
                database.connect()
                restarted = ClanBattle.__new__(ClanBattle)
                restarted.__dict__.update(self.battle.__dict__)
                restarted.group_data_list = {}
                restarted._boss_status = {100: asyncio.get_event_loop().create_future()}
                restarted.undo(100, 10)
                self.assertEqual(self.snapshot(), original)
                self.assertEqual(Clan_challenge_undo.select().count(), 0)
                database.close()

    async def test_snapshot_write_failure_rolls_back_the_entire_report(self):
        self.application()
        original = self.snapshot()
        with patch.object(Clan_challenge_undo, 'create', side_effect=RuntimeError('journal failure')):
            with self.assertRaisesRegex(RuntimeError, 'journal failure'):
                self.battle.challenge(100, 10, False, 25)
        self.assertEqual(self.snapshot(), original)
        self.assertEqual(Clan_challenge.select().count(), 0)
        self.assertFalse(self.battle._boss_status[100].done())

    async def test_failed_undo_rolls_back_record_and_snapshot_deletion(self):
        self.application()
        self.battle.challenge(100, 10, False, 25)
        current = self.snapshot()
        with patch.object(Clan_group, 'save', side_effect=RuntimeError('save failure')):
            with self.assertRaisesRegex(RuntimeError, 'save failure'):
                self.battle.undo(100, 10)
        self.assertEqual(self.snapshot(), current)
        self.assertEqual(Clan_challenge.select().count(), 1)
        self.assertEqual(Clan_challenge_undo.select().count(), 1)

    async def test_sequential_undo_and_repeated_empty_undo(self):
        self.battle.challenge(100, 10, False, 25, boss_num=1)
        self.battle.challenge(100, 10, False, 15, boss_num=1)
        self.battle.undo(100, 10)
        self.assertEqual(json.loads(self.group().now_cycle_boss_health)['1'], 75)
        self.battle.undo(100, 10)
        self.assertEqual(json.loads(self.group().now_cycle_boss_health)['1'], 100)
        with self.assertRaises(GroupError):
            self.battle.undo(100, 10)
        self.assertEqual(Clan_challenge_undo.select().count(), 0)

    async def test_archive_roundtrip_preserves_snapshot_and_clear_removes_it(self):
        self.application()
        original = self.snapshot()
        self.battle.challenge(100, 10, False, 25)
        self.battle.switch_data_slot(100, 2)
        self.battle.switch_data_slot(100, 0)
        self.battle.undo(100, 10)
        self.assertEqual(self.snapshot(), original)
        self.battle.challenge(100, 10, False, 25)
        self.battle.switch_data_slot(100, 2)
        self.battle.clear_data_slot(100, 0)
        self.assertEqual(Clan_challenge_undo.select().count(), 0)

    async def test_legacy_record_keeps_health_only_undo_with_explicit_notice(self):
        self.record(health=80)
        message = self.battle.undo(100, 10)
        self.assertIn('旧记录无状态快照', message)
        self.assertEqual(Clan_challenge.select().count(), 0)

    async def test_corrupt_snapshot_rejects_without_modifying_live_state(self):
        self.battle.challenge(100, 10, False, 25, boss_num=1)
        Clan_challenge_undo.update(before_state='{"version":99}').execute()
        current = self.snapshot()
        with self.assertRaises(GroupError):
            self.battle.undo(100, 10)
        self.assertEqual(self.snapshot(), current)
        self.assertEqual(Clan_challenge.select().count(), 1)

    async def test_rollover_undo_rejects_later_health_in_a_different_cycle(self):
        health = {str(number): 0 for number in range(1, 6)}
        health['5'] = 40
        Clan_group.update(now_cycle_boss_health=json.dumps(health)).where(Clan_group.group_id == 100).execute()
        self.battle.challenge(100, 10, True, boss_num=5)
        next_health = json.loads(self.group().next_cycle_boss_health)
        next_health['1'] = 90
        Clan_group.update(next_cycle_boss_health=json.dumps(next_health)).where(Clan_group.group_id == 100).execute()
        current = self.snapshot()
        with self.assertRaises(UndoConflict):
            self.battle.undo(100, 10)
        self.assertEqual(self.snapshot(), current)
        self.assertEqual(Clan_challenge.select().count(), 1)

    async def test_tail_undo_rejects_applications_that_would_move_to_previous_cycle(self):
        self.battle.challenge(100, 10, True, boss_num=1)
        self.battle.apply_for_challenge(False, 100, 20, '1')
        current = self.snapshot()
        with self.assertRaises(UndoConflict):
            self.battle.undo(100, 10)
        self.assertEqual(self.snapshot(), current)

    async def test_rollover_undo_restores_tree_and_reservation_with_both_health_maps(self):
        health = {str(number): 0 for number in range(1, 6)}
        health['5'] = 40
        Clan_group.update(now_cycle_boss_health=json.dumps(health)).where(Clan_group.group_id == 100).execute()
        self.application(boss='5')
        self.reserve({'5': {'10': 'rollover reservation'}})
        original = self.snapshot()
        self.battle.challenge(100, 10, True, boss_num=5)
        self.assertEqual(self.group().boss_cycle, 2)
        self.battle.undo(100, 10)
        self.assertEqual(self.snapshot(), original)

    async def test_compensation_undo_restores_application_and_available_blade(self):
        self.battle.challenge(100, 10, True, boss_num=1)
        self.battle.apply_for_challenge(True, 100, 10, '2')
        self.battle.report_hurt(2, 40, 100, 10)
        original = self.snapshot()
        self.battle.challenge(100, 10, False, 25)
        self.battle.undo(100, 10)
        self.assertEqual(self.snapshot(), original)
        self.assertEqual(count_blades(Clan_challenge.select()).compensation, 1)

    async def test_legacy_json_null_application_has_a_restorable_snapshot(self):
        Clan_group.update(challenging_member_list='null').where(Clan_group.group_id == 100).execute()
        self.battle.challenge(100, 10, False, 25, boss_num=1)
        self.battle.undo(100, 10)
        self.assertIsNone(self.group().challenging_member_list)
        self.assertEqual(json.loads(self.group().now_cycle_boss_health)['1'], 100)
