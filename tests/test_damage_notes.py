"""Damage report notes remain independent from tree notes."""
import sys
import unittest
from pathlib import Path
from unittest.mock import patch
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_battle_service as harness
from ybplugins.clan_battle.components.challenge_state import ChallengeState
from ybplugins.clan_battle.components.kernel import execute


class DamageNoteTests(unittest.IsolatedAsyncioTestCase):
    tearDown = harness.BattleServiceTests.tearDown
    group = harness.BattleServiceTests.group

    def setUp(self):
        harness.BattleServiceTests.setUp(self)
        self.battle.setting.update(public_address='http://localhost/', public_basepath='/')
        self.battle.apply_for_challenge(False, 100, 10, 1)

    def command(self, text):
        return execute(self.battle, 17, dict(message_type='group', group_id=100,
                       user_id=10, raw_message=text))

    def application(self, qqid=10):
        return ChallengeState.from_json(self.group().challenging_member_list).get(1, qqid)

    async def test_note_roundtrip_and_text_status(self):
        self.assertIn('已记录伤害', self.command('报伤害 30000w 还有一个ub'))
        info = self.application()
        self.assertEqual((info.damage, info.damage_message), (30000, '还有一个ub'))
        self.assertIn('留言：还有一个ub', '\n'.join(self.battle.challenger_info_small(self.group(), '1')))

    async def test_update_and_cancel_clear_damage_note_preserve_tree_note(self):
        self.battle.put_on_the_tree(100, 10, message='树上留言')
        self.command('报伤害 2s200w 伤害留言')
        self.assertEqual((self.application().msg, self.application().damage_message),
                         ('树上留言', '伤害留言'))
        self.command('报伤害 3s300w')
        self.assertIsNone(self.application().damage_message)
        self.command('报伤害 400w 更新留言')
        self.battle.report_hurt(0, 0, 100, 10, clean_type=1)
        info = self.application()
        self.assertEqual((info.damage, info.msg, info.damage_message), (0, '树上留言', None))

    async def test_legacy_alias_and_delegation_with_note(self):
        self.battle.apply_for_challenge(False, 100, 30, 1)
        self.command('报伤害 3s300w[CQ:at,qq=30] 代报留言')
        info = self.application(30)
        self.assertEqual((info.s, info.damage, info.damage_message), (3, 300, '代报留言'))
        self.command('打了200w')
        self.assertEqual(self.application().damage, 200)

    async def test_note_without_damage_rejected_without_mutation(self):
        original = self.group().challenging_member_list
        for text in ('报伤害 还有一个ub', '报伤害 30000w 还有一个ub[CQ:at,qq=30]'):
            self.assertIn('格式出错', self.command(text))
        self.assertEqual(self.group().challenging_member_list, original)

    async def test_status_image_contains_damage_note(self):
        self.command('报伤害 30000w 还有一个ub')
        prefix = 'ybplugins.clan_battle.components.realize.'
        with patch(prefix + 'BossStatusImageCore') as boss_block, \
             patch(prefix + 'get_process_image'), \
             patch(prefix + 'generate_combind_boss_state_image',
                   return_value=Image.new('RGB', (1, 1))):
            self.battle.challenger_info(100)
        self.assertIn('还有一个ub', boss_block.call_args_list[0].args[5]['挑战']['10'])

    async def test_report_and_undo_restore_damage_note(self):
        self.command('报伤害 30000w 还有一个ub')
        self.battle.challenge(100, 10, False, 25)
        self.assertIsNone(self.application())
        self.battle.undo(100, 10)
        self.assertEqual(self.application().damage_message, '还有一个ub')
