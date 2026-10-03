"""Group login links go only to the requester, with temporary-session fallback."""
import sys
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, call, patch

from aiocqhttp.exceptions import ActionFailed, NetworkError
from peewee import SqliteDatabase

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src' / 'client'))

from ybplugins.login import Login
from ybplugins.ybdata import User


class LoginDeliveryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.db = SqliteDatabase(':memory:')
        self.binding = self.db.bind_ctx([User])
        self.binding.__enter__()
        self.db.connect()
        self.db.create_tables([User])
        self.api = AsyncMock()
        self.login = Login({
            'super-admin': [1], 'web_mode_hint': False,
            'public_address': 'https://example.test/', 'public_basepath': '/',
        }, self.api)
        self.ctx = dict(message_type='group', group_id=100, user_id=10,
                        sender={'nickname': 'member'})

    def tearDown(self):
        self.db.close()
        self.binding.__exit__(None, None, None)

    def link(self):
        user = User.get_by_id(10)
        self.assertTrue(user.login_code_available)
        return f'https://example.test/login/c/#qqid=10&key={user.login_code}'

    async def test_friend_receives_link_without_group_reply(self):
        for command in ('登录', '登陆'):
            with self.subTest(command=command):
                self.api.reset_mock()
                result = await self.login.execute_async(Login.match(command), self.ctx)
                self.assertEqual(result, {'reply': '', 'block': True})
                self.api.send_private_msg.assert_awaited_once_with(
                    user_id=10, message=self.link())
                self.api.send_group_msg.assert_not_called()
                self.assertEqual(self.ctx['message_type'], 'group')

    async def test_nonfriend_receives_same_link_via_source_group(self):
        self.api.send_private_msg.side_effect = [
            ActionFailed({'retcode': 100, 'status': 'failed'}), {'message_id': 1}]
        result = await self.login.execute_async(1, self.ctx)
        self.assertEqual(result, {'reply': '', 'block': True})
        self.assertEqual(self.api.send_private_msg.await_args_list, [
            call(user_id=10, message=self.link()),
            call(user_id=10, group_id=100, message=self.link()),
        ])

    async def test_closed_temporary_session_returns_only_private_chat_hint(self):
        self.api.send_private_msg.side_effect = ActionFailed(
            {'retcode': 100, 'status': 'failed'})
        result = await self.login.execute_async(1, self.ctx)
        self.assertEqual(result, {'reply': '请私聊使用', 'block': True})
        self.assertEqual(self.api.send_private_msg.await_count, 2)
        self.assertNotIn(self.link(), result['reply'])

    async def test_disconnected_api_falls_back_without_exposing_link(self):
        self.api.send_private_msg.side_effect = NetworkError()
        result = await self.login.execute_async(1, self.ctx)
        self.assertEqual(result, {'reply': '请私聊使用', 'block': True})

    async def test_private_login_keeps_reply_and_hint(self):
        self.login.setting['web_mode_hint'] = True
        result = await self.login.execute_async(
            1, dict(self.ctx, message_type='private'))
        self.assertTrue(result['reply'].startswith(self.link()))
        self.assertIn('链接无法打开', result['reply'])
        self.assertTrue(result['block'])
        self.api.send_private_msg.assert_not_called()

    async def test_group_delivery_includes_web_mode_hint(self):
        self.login.setting['web_mode_hint'] = True
        await self.login.execute_async(1, self.ctx)
        message = self.api.send_private_msg.await_args.kwargs['message']
        self.assertTrue(message.startswith(self.link()))
        self.assertIn('链接无法打开', message)

    async def test_password_reset_still_requires_private_chat(self):
        with patch.object(self.login, '_reset_pwd', return_value='temporary') as reset:
            result = await self.login.execute_async(3, self.ctx)
            self.assertEqual(result, {'reply': '请私聊使用', 'block': True})
            reset.assert_not_called()
            result = await self.login.execute_async(
                3, dict(self.ctx, message_type='private'))
            self.assertEqual(result['reply'], '您的临时密码是：temporary')
        self.api.send_private_msg.assert_not_called()
