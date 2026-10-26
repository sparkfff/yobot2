"""Password logins opt into persistent cookies; login links keep their expiry."""
import sys
import time
import unittest
from http.cookies import SimpleCookie
from pathlib import Path
from unittest.mock import AsyncMock, patch

from peewee import SqliteDatabase
from quart import Quart

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src' / 'client'))
from ybplugins.login import EXPIRED_TIME, LOGIN_AUTH_COOKIE_NAME, Login, _add_salt_and_hash
from ybplugins.ybdata import User, User_login


class RememberLoginTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.db = SqliteDatabase(':memory:')
        self.binding = self.db.bind_ctx([User, User_login])
        self.binding.__enter__()
        self.db.connect()
        self.db.create_tables([User, User_login])
        User.create(qqid=10, privacy=0, salt='test-salt',
                    password=_add_salt_and_hash('frontend-hash', 'test-salt'),
                    must_change_password=False, login_code='key123',
                    login_code_available=True, login_code_expire_time=int(time.time()) + 60)
        self.app = Quart(__name__)
        self.app.secret_key = 'test-only'
        self.app.config['TESTING'] = True
        self.plugin = Login({'public_basepath': '/', 'preffix_on': False}, None)
        self.plugin.register_routes(self.app)
        self.client = self.app.test_client()

    def tearDown(self):
        self.db.close()
        self.binding.__exit__(None, None, None)

    def auth_cookie(self, response):
        cookies = SimpleCookie()
        for value in response.headers.getlist('Set-Cookie'):
            cookies.load(value)
        return cookies[LOGIN_AUTH_COOKIE_NAME]

    async def test_unchecked_password_login_uses_browser_session_cookie(self):
        for remember in (None, '0', 'false'):
            with self.subTest(remember=remember):
                form = {'qqid': '10', 'pwd': 'frontend-hash'}
                if remember is not None:
                    form['remember'] = remember
                response = await self.client.post('/login/', form=form)
                self.assertEqual(response.status_code, 302)
                cookie = self.auth_cookie(response)
                self.assertEqual(cookie['max-age'], '')
                self.assertEqual(cookie['expires'], '')
                async with self.client.session_transaction() as sess:
                    self.assertEqual(sess['yobot_user'], 10)
                    self.assertFalse(sess.permanent)

    async def test_remembered_password_login_survives_session_loss(self):
        response = await self.client.post('/login/', form={
            'qqid': '10', 'pwd': 'frontend-hash', 'remember': '1'})
        cookie = self.auth_cookie(response)
        self.assertEqual(int(cookie['max-age']), EXPIRED_TIME)
        self.assertTrue(cookie['expires'])
        async with self.client.session_transaction() as sess:
            sess.clear()
        response = await self.client.get('/login/')
        self.assertEqual(response.status_code, 302)
        async with self.client.session_transaction() as sess:
            self.assertEqual(sess['yobot_user'], 10)

    async def test_login_link_preserves_persistent_cookie(self):
        response = await self.client.get('/login/?qqid=10&key=key123')
        self.assertEqual(response.status_code, 302)
        self.assertEqual(int(self.auth_cookie(response)['max-age']), EXPIRED_TIME)
        self.assertFalse(User.get_by_id(10).login_code_available)

    async def test_unchecking_overwrites_previous_persistent_cookie(self):
        await self.client.post('/login/', form={
            'qqid': '10', 'pwd': 'frontend-hash', 'remember': '1'})
        response = await self.client.post('/login/', form={
            'qqid': '10', 'pwd': 'frontend-hash', 'remember': '0'})
        cookie = self.auth_cookie(response)
        self.assertEqual(cookie['max-age'], '')
        self.assertEqual(cookie['expires'], '')

    async def test_invalid_password_does_not_issue_cookie(self):
        with patch('ybplugins.login.render_template', new=AsyncMock(return_value='invalid')):
            response = await self.client.post('/login/', form={
                'qqid': '10', 'pwd': 'wrong', 'remember': '1'})
        self.assertEqual(User_login.select().count(), 0)
        self.assertFalse(any(value.startswith(LOGIN_AUTH_COOKIE_NAME + '=')
                             for value in response.headers.getlist('Set-Cookie')))
