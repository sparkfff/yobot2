"""Exercise registered routes against isolated users, clans and configuration."""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from peewee import SqliteDatabase
from quart import Quart

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src' / 'client'))

from ybplugins.settings import Setting
from ybplugins.ybdata import Clan_group, Clan_member, User
from ybplugins.clan_battle.components import kernel, web_operation


class PermissionRoutesTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = SqliteDatabase(':memory:')
        self.binding = self.db.bind_ctx([User, Clan_group, Clan_member])
        self.binding.__enter__()
        self.db.connect()
        self.db.create_tables([User, Clan_group, Clan_member])
        User.create(qqid=1, authority_group=1)
        User.create(qqid=10, authority_group=10)
        User.create(qqid=20, authority_group=100)
        User.create(qqid=30, authority_group=100)
        Clan_group.create(group_id=100, group_name='A', notification=0)
        Clan_group.create(group_id=200, group_name='B', notification=0)
        Clan_member.create(group_id=100, qqid=10, role=10)
        Clan_member.create(group_id=200, qqid=10, role=100)
        Clan_member.create(group_id=200, qqid=20, role=10)
        Clan_member.create(group_id=200, qqid=30, role=100)
        self.setting = {
            'dirname': self.tmp.name, 'verinfo': {}, 'host': 'localhost',
            'port': 9222, 'access_token': 'private', 'public_basepath': '/',
            'public_address': 'http://localhost/', 'super-admin': [1],
            'web_mode_hint': True, 'runtime_only': 'internal',
        }
        self.app = Quart(__name__)
        self.app.secret_key = 'test-only'
        self.app.config['TESTING'] = True
        self.plugin = Setting(self.setting, None, {})
        self.plugin.register_routes(self.app)
        self.clan = SimpleNamespace(
            setting=self.setting,
            get_clan_group=lambda group_id: Clan_group.get_or_none(group_id=group_id),
            modify=Mock(return_value='updated'),
            _boss_data_dict=Mock(return_value={}),
            cancel_blade=Mock(return_value='cancelled'),
        )
        web_operation.register_routes(self.clan, self.app)
        self.client = self.app.test_client()

    def tearDown(self):
        self.db.close()
        self.binding.__exit__(None, None, None)
        self.tmp.cleanup()

    async def login(self, qqid):
        async with self.client.session_transaction() as sess:
            sess.clear()
            if qqid is not None:
                sess['yobot_user'] = qqid
                sess['csrf_token'] = 'test-csrf'

    async def post(self, url, payload=None):
        payload = dict(payload or {})
        payload['csrf_token'] = 'test-csrf'
        response = await self.client.post(url, json=payload)
        return await response.get_json()

    async def test_global_settings_denies_guild_admin_on_page_and_api(self):
        await self.login(10)
        with patch('ybplugins.settings.render_template', new=AsyncMock(return_value='denied')) as render:
            await self.client.get('/admin/setting/')
            self.assertEqual(render.call_args.args[0], 'unauthorized.html')
        response = await self.client.get('/admin/setting/api/')
        self.assertEqual((await response.get_json())['code'], 11)
        response = await self.client.put('/admin/setting/api/', json={
            'csrf_token': 'test-csrf', 'setting': {'web_mode_hint': False}})
        self.assertEqual((await response.get_json())['code'], 11)
        self.assertTrue(self.setting['web_mode_hint'])

    async def test_auto_update_requires_owner_before_network_or_write(self):
        with patch('ybplugins.settings.create_session', side_effect=AssertionError('unexpected network')):
            for qqid, code in [(None, 10), (10, 11), (20, 11), (30, 11)]:
                await self.login(qqid)
                result = await self.post('/admin/setting/auto_get_boss_data/')
                self.assertEqual(result['code'], code)
        self.assertFalse((Path(self.tmp.name) / 'yobot_config.json').exists())

    async def test_owner_config_schema_and_valid_persistence(self):
        await self.login(1)
        response = await self.client.get('/admin/setting/api/')
        exposed = (await response.get_json())['settings']
        self.assertNotIn('runtime_only', exposed)
        self.assertNotIn('dirname', exposed)
        self.assertNotIn('access_token', exposed)
        for value in ([], None, {'dirname': 'changed'}, {'verinfo': {}}, {'unknown': 1}):
            response = await self.client.put('/admin/setting/api/', json={
                'csrf_token': 'test-csrf', 'setting': value})
            self.assertEqual((await response.get_json())['code'], 30)
        self.assertEqual(self.setting['dirname'], self.tmp.name)
        response = await self.client.put('/admin/setting/api/', json={
            'csrf_token': 'test-csrf', 'setting': {'web_mode_hint': False}})
        self.assertEqual((await response.get_json())['code'], 0)
        saved = json.loads((Path(self.tmp.name) / 'yobot_config.json').read_text())
        self.assertFalse(saved['web_mode_hint'])

    async def test_global_settings_rejects_invalid_body_and_csrf(self):
        await self.login(1)
        for body in ([], {'setting': {'web_mode_hint': False}}):
            response = await self.client.put('/admin/setting/api/', json=body)
            self.assertIn((await response.get_json())['code'], (15, 30))
        self.assertTrue(self.setting['web_mode_hint'])
        async with self.client.session_transaction() as sess:
            del sess['csrf_token']
        response = await self.client.put('/admin/setting/api/', json={
            'setting': {'web_mode_hint': False}})
        self.assertEqual((await response.get_json())['code'], 15)
        self.assertTrue(self.setting['web_mode_hint'])

    async def test_admin_of_other_clan_cannot_modify_current_clan(self):
        await self.login(10)
        result = await self.post('/clan/200/api/', {'action': 'modify'})
        self.assertEqual(result['code'], 11)
        self.clan.modify.assert_not_called()
        result = await self.post('/clan/200/setting/api/', {'action': 'get_setting'})
        self.assertEqual(result['code'], 11)

    async def test_membership_admin_allowed_with_global_member_role(self):
        await self.login(20)
        result = await self.post('/clan/200/api/', {'action': 'modify', 'cycle': 1, 'bossData': []})
        self.assertEqual(result['code'], 0)
        self.clan.modify.assert_called_once_with(200, cycle=1, bossData=[])
        result = await self.post('/clan/200/setting/api/', {'action': 'get_setting'})
        self.assertEqual(result['code'], 0)

    async def test_owner_allowed_without_clan_membership(self):
        await self.login(1)
        result = await self.post('/clan/200/api/', {'action': 'get_data'})
        self.assertEqual(result['code'], 0)
        self.assertTrue(result['selfData']['is_admin'])
        result = await self.post('/clan/200/setting/api/', {'action': 'get_setting'})
        self.assertEqual(result['code'], 0)

    async def test_deleted_owner_cannot_use_global_or_clan_management(self):
        User.update(deleted=True).where(User.qqid == 1).execute()
        await self.login(1)
        response = await self.client.get('/admin/setting/api/')
        self.assertEqual((await response.get_json())['code'], 11)
        result = await self.post('/clan/200/setting/api/', {'action': 'get_setting'})
        self.assertEqual(result['code'], 11)

    async def test_other_clan_admin_cannot_view_without_membership(self):
        await self.login(10)
        Clan_member.delete().where(Clan_member.group_id == 200, Clan_member.qqid == 10).execute()
        result = await self.post('/clan/200/api/', {'action': 'get_data'})
        self.assertEqual(result['code'], 11)

    def test_cancel_all_checks_actor_not_delegated_target(self):
        ctx = {'message_type': 'group', 'raw_message': '取消出刀all [CQ:at,qq=1]',
               'group_id': 200, 'user_id': 10, 'sender': {'role': 'member'}}
        result = kernel.execute(self.clan, 13, ctx)
        self.assertIn('管理员', result)
        self.clan.cancel_blade.assert_not_called()
        ctx['sender']['role'] = 'admin'
        kernel.execute(self.clan, 13, ctx)
        self.clan.cancel_blade.assert_called_once_with(200, 1, cancel_type=0)

    def test_permission_command_updates_only_this_membership(self):
        ctx = {'message_type': 'group', 'raw_message': '权限',
               'group_id': 200, 'user_id': 10,
               'sender': {'role': 'member', 'nickname': 'member'}}
        kernel.execute(self.clan, 18, ctx)
        self.assertEqual(Clan_member.get(group_id=100, qqid=10).role, 10)
        self.assertEqual(Clan_member.get(group_id=200, qqid=10).role, 100)
        self.assertEqual(User.get_by_id(10).authority_group, 10)
        ctx['user_id'] = 1
        kernel.execute(self.clan, 18, ctx)
        self.assertEqual(User.get_by_id(1).authority_group, 1)
        self.assertEqual(Clan_member.get(group_id=200, qqid=1).role, 100)


if __name__ == '__main__':
    unittest.main()
