"""Group switches survive reload and can restore a disabled group."""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src' / 'client'))

from yobot import Yobot


class GroupSwitchTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.bot = Yobot.__new__(Yobot)
        self.bot.glo_setting = {
            'dirname': self.directory.name, 'verinfo': {}, 'super-admin': [1],
            'white_list_mode': False, 'black-list-group': [200],
            'white-list-group': [], 'preffix_on': False,
        }
        self.bot.black_list = set()
        self.plugin = AsyncMock()
        self.plugin.execute_async.return_value = '正常响应'
        self.bot.plug_new = [self.plugin]
        self.bot.plug_passive = []

    async def send(self, command, role='admin', group=100, user=10, kind='group'):
        return await self.bot.proc_async({
            'raw_message': command, 'message_type': kind, 'group_id': group,
            'user_id': user, 'sender': {'user_id': user, 'role': role},
        })

    async def test_disable_restore_and_persistence(self):
        self.assertIn('已关闭', await self.send('关闭 yobot2'))
        self.assertIsNone(await self.send('help'))
        self.plugin.execute_async.assert_not_awaited()
        with open(Path(self.directory.name) / 'yobot_config.json', encoding='utf-8') as saved:
            reloaded = json.load(saved)
        self.assertNotIn('dirname', reloaded)
        self.assertNotIn('verinfo', reloaded)
        self.bot.glo_setting.update(reloaded)
        self.assertIsNone(await self.send('help'))
        self.assertEqual(await self.send('help', group=300), '正常响应')
        self.assertIn('已开启', await self.send('开启 yobot2'))
        self.assertEqual(await self.send('help'), '正常响应')
        self.assertEqual(self.bot.glo_setting['black-list-group'], [200])
        with open(Path(self.directory.name) / 'yobot_config.json', encoding='utf-8') as saved:
            self.assertEqual(json.load(saved)['black-list-group'], [200])

    async def test_whitelist_mode(self):
        self.bot.glo_setting['white_list_mode'] = True
        self.bot.glo_setting['white-list-group'] = [200]
        self.assertIsNone(await self.send('help'))
        self.assertIn('已开启', await self.send('开启 yobot2'))
        self.assertEqual(await self.send('help'), '正常响应')
        self.assertIn('已关闭', await self.send('关闭 yobot2'))
        self.assertIsNone(await self.send('help'))
        self.assertEqual(self.bot.glo_setting['white-list-group'], [200])

    async def test_permissions_and_private_chat(self):
        self.assertIn('只有', await self.send('关闭 yobot2', role='member'))
        self.assertIn('仅可用于群聊', await self.send('关闭 yobot2', kind='private'))
        self.assertEqual(self.bot.glo_setting['black-list-group'], [200])
        self.assertIn('已关闭', await self.send('关闭 yobot2', role='owner'))
        self.assertIn('已开启', await self.send('开启 yobot2', role='member', user=1))

    async def test_controls_with_prefix_enabled(self):
        self.bot.glo_setting.update(preffix_on=True, preffix_string='!')
        self.assertIn('已关闭', await self.send('关闭 yobot2'))
        self.assertIsNone(await self.send('!help'))
        self.assertIn('已开启', await self.send('!开启 yobot2'))
        self.assertEqual(await self.send('!help'), '正常响应')

    async def test_save_failure_keeps_state(self):
        with patch('ybplugins.group_switch.os.replace', side_effect=OSError):
            self.assertIn('状态未改变', await self.send('关闭 yobot2'))
        self.assertEqual(self.bot.glo_setting['black-list-group'], [200])
        self.assertEqual(list(Path(self.directory.name).iterdir()), [])
        self.assertEqual(await self.send('help'), '正常响应')

    async def test_backend_group_list_change_is_immediate(self):
        self.bot.glo_setting['black-list-group'] = [100]
        self.assertIsNone(await self.send('help'))
        self.bot.glo_setting['black-list-group'] = []
        self.assertEqual(await self.send('help'), '正常响应')

    async def test_user_blacklist_still_applies_to_controls(self):
        self.bot.black_list.add(10)
        self.assertIsNone(await self.send('关闭 yobot2'))
        self.assertEqual(self.bot.glo_setting['black-list-group'], [200])


if __name__ == '__main__':
    unittest.main()
