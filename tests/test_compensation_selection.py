"""Selected compensation sources persist across applications, reports and undo."""
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_battle_service as harness
from ybplugins.ybdata import Clan_challenge, Clan_group
from ybplugins.clan_battle.components.kernel import execute
from ybplugins.clan_battle.components.challenge_state import ChallengeState
from ybplugins.clan_battle.components.tail_return_seconds import pending_compensations
from ybplugins.clan_battle.exception import GroupError, InputError


class CompensationSelectionTests(unittest.IsolatedAsyncioTestCase):
    setUp = harness.BattleServiceTests.setUp
    tearDown = harness.BattleServiceTests.tearDown
    group = harness.BattleServiceTests.group

    def tail(self, seconds, qqid=10, date=harness.TODAY, bid=0):
        return Clan_challenge.create(gid=100, bid=bid, qqid=qqid,
            challenge_pcrdate=date, challenge_pcrtime=1, boss_cycle=1, boss_num=1,
            boss_health_remain=0, challenge_damage=20, is_continue=False, return_seconds=seconds)

    def command(self, text, number):
        self.battle.setting.update(public_address='http://localhost/', public_basepath='/')
        return execute(self.battle, number, dict(message_type='group', group_id=100,
                                               user_id=10, raw_message=text))

    def pending(self):
        records = Clan_challenge.select().where(Clan_challenge.qqid == 10)
        return [tail.cid for tail in pending_compensations(self.battle, records, 'cn')]

    async def test_report_selects_second_tail_then_default_consumes_first_and_undo(self):
        first = self.tail(60)
        selected = self.tail(80)
        reply = self.command('报刀 1 1b 80s', 4)
        self.assertIn('已消耗补偿：80s', reply)
        record = Clan_challenge.select().order_by(Clan_challenge.cid.desc()).get()
        self.assertEqual(record.consumed_tail_id, selected.cid)
        self.assertEqual(self.pending(), [first.cid])
        self.battle.undo(100, 10)
        self.assertEqual(self.pending(), [first.cid, selected.cid])
        self.command('报刀 -1 1 b', 4)
        self.assertEqual(self.pending(), [selected.cid])

    async def test_application_remembers_source_even_when_seconds_edited_and_tail_report(self):
        first = self.tail(60)
        selected = self.tail(80)
        self.assertIn('已选定补偿：80s', self.command('进1b 80s', 12))
        state = ChallengeState.from_json(self.group().challenging_member_list)
        self.assertEqual(state.get(1, 10).compensation_tail_id, selected.cid)
        Clan_challenge.update(return_seconds=81).where(Clan_challenge.cid == selected.cid).execute()
        reply = self.command('尾刀 1 b', 5)
        self.assertIn('已消耗补偿：81s', reply)
        report = Clan_challenge.select().order_by(Clan_challenge.cid.desc()).get()
        self.assertEqual(report.consumed_tail_id, selected.cid)
        self.assertEqual(self.pending(), [first.cid])
        self.battle.undo(100, 10)
        self.assertEqual(ChallengeState.from_json(self.group().challenging_member_list).get(1, 10).compensation_tail_id, selected.cid)

    async def test_explicit_report_overrides_application_and_duplicate_seconds_use_oldest(self):
        first = self.tail(60)
        self.tail(80)
        self.tail(80)
        self.command('进1b 60s', 12)
        self.command('报刀 1 1b 80s', 4)
        report = Clan_challenge.select().order_by(Clan_challenge.cid.desc()).get()
        self.assertEqual(report.consumed_tail_id, first.cid + 1)
        self.assertEqual(self.pending(), [first.cid, first.cid + 2])

    async def test_selected_application_is_used_with_or_without_explicit_boss(self):
        self.tail(60)
        selected = self.tail(80)
        for report in ('报刀 1', '报刀 1 1'):
            self.command('进1b 80s', 12)
            self.assertIn('已消耗补偿：80s', self.command(report, 4))
            record = Clan_challenge.select().order_by(Clan_challenge.cid.desc()).get()
            self.assertEqual(record.consumed_tail_id, selected.cid)
            self.battle.undo(100, 10)
            self.battle.cancel_blade(100, 10)

    async def test_missing_invalid_seconds_and_stale_application_never_mutate(self):
        first = self.tail(60)
        original = self.group().now_cycle_boss_health
        for text in ('报刀 1 1b 80s', '报刀 1 1b 20s', '报刀 1 1 80s'):
            reply = self.command(text, 4)
            self.assertTrue('没有可用' in reply or '21至90' in reply or '请加b' in reply)
            self.assertEqual(Clan_challenge.select().count(), 1)
            self.assertEqual(self.group().now_cycle_boss_health, original)
        self.tail(90)
        self.command('进1b 60s', 12)
        self.battle.challenge(100, 10, False, 1, boss_num=1, is_continue=True)
        # Restore a stale application referencing the already consumed source.
        state = ChallengeState()
        state.apply(1, 10, True).compensation_tail_id = first.cid
        Clan_group.update(challenging_member_list=state.to_json()).where(Clan_group.group_id == 100).execute()
        self.battle.group_data_list.clear()
        with self.assertRaisesRegex(GroupError, '已使用或已撤销'):
            self.battle.challenge(100, 10, False, 1)

    async def test_delegation_yesterday_and_default_bc_match(self):
        previous = self.tail(80, qqid=30, date=harness.TODAY - 1)
        reply = self.command('报刀 1 1b [CQ:at,qq=30] 昨日 80s', 4)
        self.assertIn('已消耗补偿：80s', reply)
        self.assertEqual(Clan_challenge.select().order_by(Clan_challenge.cid.desc()).get().consumed_tail_id, previous.cid)
        default = self.tail(None)
        self.assertIn('已选定补偿：90s', self.command('进1b 90s', 12))
        self.assertEqual(ChallengeState.from_json(self.group().challenging_member_list).get(1, 10).compensation_tail_id, default.cid)

    async def test_exact_requested_command_parser(self):
        with patch.object(self.battle, 'challenge', return_value='ok') as report:
            self.assertEqual(self.command('报刀 1 3000wb 80s', 4), 'ok')
            self.assertEqual(report.call_args.args[3], 30000000)
            self.assertEqual(report.call_args.kwargs['compensation_seconds'], 80)
        with patch.object(self.battle, 'apply_for_challenge', return_value='ok') as apply:
            self.assertEqual(self.command('进1b 80s', 12), 'ok')
            self.assertEqual(apply.call_args.kwargs['compensation_seconds'], 80)

    async def test_seconds_imply_compensation_without_b(self):
        first = self.tail(60)
        selected = self.tail(80)
        self.assertIn('已选定补偿：80s', self.command('进1 80s', 12))
        self.assertTrue(ChallengeState.from_json(self.group().challenging_member_list).get(1, 10).is_continue)
        self.assertIn('已消耗补偿：80s', self.command('报刀 1 1 80s', 4))
        record = Clan_challenge.select().order_by(Clan_challenge.cid.desc()).get()
        self.assertTrue(record.is_continue)
        self.assertEqual(record.consumed_tail_id, selected.cid)
        self.assertEqual(self.pending(), [first.cid])
