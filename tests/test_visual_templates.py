"""Shared templates must load one stable theme at the configured base path."""
from html.parser import HTMLParser
from pathlib import Path
import sys
import tempfile
import unittest

from quart import Quart

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src/client'))
from ybplugins.file_access import register_file_routes
from ybplugins.templating import render_template


class Elements(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.tags = []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))


class VisualTemplateTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.app = Quart(__name__)
        self.app.secret_key = 'test'
        self.public = Path(__file__).resolve().parents[1] / 'src/client/public'
        register_file_routes(self.app, '/bot/', self.public / 'libs',
                             self.public / 'static', self.tmp.name)
        for endpoint in ('yobot_user', 'yobot_api_getdomain', 'yobot_setting_api',
                         'yobot_users_api', 'yobot_login'):
            self.app.add_url_rule('/bot/' + endpoint, endpoint, lambda: '')

    async def test_main_pages_load_same_versioned_theme_and_responsive_viewport(self):
        for template in ('admin/setting.html', 'admin/users.html', 'clan/panel.html',
                         'clan/setting.html', 'clan/progress.html', 'clan/subscribers.html',
                         'clan/statistics.html', 'clan/statistics/statistics2.html', 'login.html'):
            with self.subTest(template=template):
                async with self.app.test_request_context('/bot/'):
                    html = await render_template(template, is_member=True, allow_api=False)
                tags = Elements(html).tags
                themes = [attrs['href'] for tag, attrs in tags if tag == 'link'
                          and '/ui/theme.css' in attrs.get('href', '')]
                self.assertEqual(len(themes), 1)
                self.assertTrue(themes[0].startswith('/bot/assets/ui/theme.css?v='))
                self.assertNotIn('/princessadventure/yocool.js', html)
                self.assertIn(('html', {'lang': 'zh-CN', 'class': 'yobot-ui'}), tags)
                viewport = [attrs['content'] for tag, attrs in tags if tag == 'meta'
                            and attrs.get('name') == 'viewport']
                self.assertEqual(viewport, ['width=device-width, initial-scale=1'])
                self.assertEqual(sum(tag == 'footer' for tag, _ in tags), 1)
                response = await self.app.test_client().get(themes[0])
                self.assertEqual(response.status_code, 200)

    async def test_viewer_panel_keeps_read_only_boundary(self):
        async with self.app.test_request_context('/bot/'):
            html = await render_template('clan/panel.html', is_member=False)
        self.assertNotIn('@click="recordDamage(', html)
        self.assertNotIn('@click="challengeapply(', html)
        self.assertIn('非公会战成员只允许查看', html)
