import json
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src' / 'client'))
from ybplugins.clan_battle.components.challenge_state import ApplicationState, ChallengeState
from ybplugins.clan_battle.exception import GroupError
import test_battle_service as service_tests


class ChallengeStateTests(unittest.TestCase):
    def test_legacy_roundtrip_preserves_nulls_delegation_and_extra_fields(self):
        data = {'1': {'123': {'is_continue': True, 'behalf': 456, 's': None,
                             'damage': None, 'tree': True, 'msg': None,
                             'legacy': {'note': '保留'}}}, '2': {}}
        state = ChallengeState.from_json(json.dumps(data))
        self.assertIsInstance(state.get(1, 123), ApplicationState)
        self.assertEqual(json.loads(state.to_json()), data)
        self.assertEqual(state.find_member('123')[0], '1')

    def test_empty_inputs_and_empty_serialization(self):
        for text in (None, '', '{}', 'null'):
            state = ChallengeState.from_json(text)
            self.assertEqual(state.to_json(), '{}')
            self.assertIsNone(state.to_json_or_none())
            self.assertIsNone(state.find_member(1))

    def test_apply_damage_tree_and_remove_are_isolated_by_member(self):
        state = ChallengeState()
        state.apply(1, 123, is_continue=True, behalf=456)
        other = state.apply('1', '456')
        state.apply(2, 789)
        state.report_damage('123', 5, 200)
        state.put_on_tree(123, None)
        own = state.get('1', '123')
        self.assertEqual((own.s, own.damage, own.tree, own.msg), (5, 200, True, None))
        self.assertEqual((other.s, other.damage, other.tree), (0, 0, False))
        state.take_off_tree(123)
        self.assertEqual((own.tree, own.msg), (False, None))
        self.assertEqual(own.damage, 200)
        self.assertIs(state.remove_member('123'), own)
        self.assertIn('1', state.applications)
        state.remove_member(456)
        self.assertNotIn('1', state.applications)
        self.assertEqual(list(state.remove_boss(2)), ['789'])
        self.assertIsNone(state.to_json_or_none())

    def test_duplicates_and_missing_members_do_not_mutate_state(self):
        state = ChallengeState()
        state.apply('1', 123)
        state.put_on_tree(123, '树上留言')
        original = state.to_json()
        with self.assertRaises(ValueError):
            state.apply(2, '123')
        with self.assertRaises(ValueError):
            state.put_on_tree(123, '覆盖')
        for operation in (lambda: state.report_damage(456, 1, 20),
                          lambda: state.put_on_tree(456),
                          lambda: state.take_off_tree(456)):
            with self.assertRaises(KeyError):
                operation()
        self.assertIsNone(state.remove_member(456))
        self.assertEqual(state.remove_boss(5), {})
        self.assertEqual(state.to_json(), original)

    def test_roundtrip_does_not_alias_mutable_legacy_values(self):
        info = ApplicationState.from_dict({'legacy': {'value': [1]}})
        serialized = info.to_dict()
        serialized['legacy']['value'].append(2)
        self.assertEqual(info.extra['legacy']['value'], [1])


class ChallengeAdapterTests(unittest.IsolatedAsyncioTestCase):
    # Reuse the real SQLite/ClanBattle fixture without inheriting its tests.
    setUp = service_tests.BattleServiceTests.setUp
    tearDown = service_tests.BattleServiceTests.tearDown
    group = service_tests.BattleServiceTests.group

    async def test_public_methods_preserve_application_damage_and_tree_behavior(self):
        self.battle.apply_for_challenge(False, 100, 10, 1)
        self.battle.apply_for_challenge(False, 100, 30, '2')
        self.assertTrue(self.battle.check_blade(100, 10))
        self.assertEqual(self.battle.get_in_boss_num(100, 10), '1')
        self.battle.report_hurt(5, 200, 100, 10)
        self.battle.put_on_the_tree(100, 10)
        self.assertEqual(self.battle.check_tree(100, 10), 1)
        self.assertFalse(self.battle.check_tree(100, 30))
        state = ChallengeState.from_json(self.group().challenging_member_list)
        self.assertEqual((state.get(1, 10).damage, state.get(1, 10).msg), (200, None))
        self.battle.take_it_of_the_tree(100, 10)
        self.assertFalse(self.battle.check_tree(100, 10))
        state = ChallengeState.from_json(self.group().challenging_member_list)
        self.assertEqual((state.get(1, 10).damage, state.get(1, 10).s), (200, 5))
        self.battle.report_hurt(0, 0, 100, 10, clean_type=1)
        self.battle.cancel_blade(100, 10)
        self.assertFalse(self.battle.check_blade(100, 10))
        self.assertTrue(self.battle.check_blade(100, 30))
        self.battle.cancel_blade(100, 30, boss_num=2, cancel_type=2)
        self.assertEqual(self.group().challenging_member_list, '{}')

    async def test_duplicate_tree_request_rolls_back_without_losing_notes(self):
        self.battle.apply_for_challenge(False, 100, 10, 1)
        self.battle.put_on_the_tree(100, 10, message='原备注')
        original = self.group().challenging_member_list
        with self.assertRaises(GroupError):
            self.battle.put_on_the_tree(100, 10, message='新备注')
        self.assertEqual(self.group().challenging_member_list, original)

    async def test_cancel_all_and_last_member_keep_null_storage_convention(self):
        self.battle.apply_for_challenge(False, 100, 10, 1)
        self.battle.cancel_blade(100, 10)
        self.assertIsNone(self.group().challenging_member_list)
        self.battle.apply_for_challenge(False, 100, 10, 1)
        self.battle.cancel_blade(100, 10, cancel_type=0)
        self.assertIsNone(self.group().challenging_member_list)
