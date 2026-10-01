"""QQ parsing and persistent tail return seconds use real battle records."""
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_battle_service as harness
from ybplugins.ybdata import Clan_challenge
from ybplugins.clan_battle.components.kernel import execute
from ybplugins.clan_battle.exception import InputError


class TailSecondsTests(unittest.IsolatedAsyncioTestCase):
    tearDown = harness.BattleServiceTests.tearDown
    group = harness.BattleServiceTests.group
    application = harness.BattleServiceTests.application

    def setUp(self):
        harness.BattleServiceTests.setUp(self)
        self.battle.setting.update(public_address='http://localhost/', public_basepath='/')

    def command(self, text, match_num=5):
        return execute(self.battle, match_num, dict(message_type='group',
                       group_id=100, user_id=10, raw_message=text))

    async def test_seconds_do_not_become_boss_number_and_survive_reload(self):
        self.application()
        reply = self.command('尾刀 41s')
        self.assertIn('返秒：41s', reply)
        record = Clan_challenge.get()
        self.assertEqual(record.return_seconds, 41)
        self.assertEqual(record.boss_num, 1)
        self.assertIsNone(record.message)
        self.battle.group_data_list.clear()
        self.assertEqual(Clan_challenge.get_by_id(record.cid).return_seconds, 41)
        self.battle.undo(100, 10)
        self.assertEqual(Clan_challenge.select().count(), 0)
        self.assertIn('preserve me', self.group().challenging_member_list)

    async def test_legacy_and_explicit_boss_seconds_formats(self):
        for text, boss, seconds in [('尾刀 1', '1', None), ('尾刀 1 41s', '1', 41),
                                    ('尾刀41s', None, 41), ('尾刀 4s', None, 4),
                                    ('尾刀 90秒', None, 90), ('尾刀 0S', None, 0),
                                    ('尾刀 1 41s bc', '1', 41),
                                    ('尾刀 1 b [CQ:at,qq=30] 昨日 41s', '1', 41)]:
            with self.subTest(text=text), patch.object(self.battle, 'challenge', return_value='ok') as report:
                self.assertEqual(self.command(text), 'ok')
                self.assertEqual(report.call_args.kwargs['boss_num'], boss)
                self.assertEqual(report.call_args.kwargs['return_seconds'], seconds)
        self.assertEqual(report.call_args.args[4], 30)
        self.assertTrue(report.call_args.args[5])
        self.assertTrue(report.call_args.kwargs['previous_day'])

    async def test_invalid_seconds_and_report_notes_never_write(self):
        self.application()
        original = self.group().challenging_member_list
        for text in ['尾刀 91s', '尾刀 -1s', '尾刀 1.5s', '尾刀 41s 42s',
                     '尾刀 1 :留言', '尾刀 1 ：留言']:
            with self.subTest(text=text):
                reply = self.command(text)
                self.assertTrue('格式' in reply or '返秒必须' in reply)
                self.assertEqual(Clan_challenge.select().count(), 0)
                self.assertEqual(self.group().challenging_member_list, original)
        self.assertIn('报刀格式', self.command('报刀 -1 1 :留言', 4))
        for seconds in [True, '41', -1, 91]:
            with self.assertRaises(InputError):
                self.battle.challenge(100, 10, True, boss_num=1, return_seconds=seconds)
        with self.assertRaises(InputError):
            self.battle.challenge(100, 10, False, 1, boss_num=1, return_seconds=41)

    async def test_old_tail_without_seconds_still_works(self):
        self.command('尾刀 1')
        self.assertIsNone(Clan_challenge.get().return_seconds)

