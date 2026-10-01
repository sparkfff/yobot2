"""Templates must stop reusing old scripts after an in-place code update."""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from quart import Quart

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src' / 'client'))
from ybplugins import templating
from ybplugins.file_access import register_file_routes


class AssetVersionTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.app = Quart(__name__)
        self.app.secret_key = 'test'
        register_file_routes(self.app, '/bot/', self.root, self.root, self.root / 'output')
        self.app.add_url_rule('/bot/user/', 'yobot_user', lambda: '')
        self.app.add_url_rule('/bot/api/getdomain/', 'yobot_api_getdomain', lambda: '')
        self.app.add_url_rule('/bot/admin/setting/api/', 'yobot_setting_api', lambda: '')
        templating._asset_digest.cache_clear()
        self.addCleanup(templating._asset_digest.cache_clear)

    async def asset_url(self, filename):
        async with self.app.test_request_context('/bot/admin/setting/'):
            return templating._vertioned_url_for('yobot_static', filename=filename)

    async def test_content_update_changes_url_without_release_version_change(self):
        script = self.root / 'setting.js'
        script.write_text('var value = 1;', encoding='utf-8')
        with patch.object(templating, 'static_folder', str(self.root)), \
                patch.object(templating, 'Ver', '4.0.2'):
            before = await self.asset_url('setting.js')
            self.assertEqual(before, await self.asset_url('setting.js'))
            modified = script.stat().st_mtime_ns
            script.write_text('var value = 2;', encoding='utf-8')
            os.utime(script, ns=(modified + 1000000000, modified + 1000000000))
            after = await self.asset_url('setting.js')
            self.assertNotEqual(before, after)
            self.assertTrue(parse_qs(urlsplit(after).query)['v'][0].startswith('4.0.2-'))
            response = await self.app.test_client().get(after)
            self.assertEqual(response.status_code, 200)
            self.assertIn(b'value = 2', await response.get_data())

    async def test_settings_template_uses_fingerprint_of_current_script(self):
        public = Path(__file__).resolve().parents[1] / 'src/client/public'
        with patch.object(templating, 'static_folder', str(public / 'static')):
            async with self.app.test_request_context('/bot/admin/setting/'):
                html = await templating.render_template('admin/setting.html')
                expected = templating._vertioned_url_for('yobot_static', filename='admin/setting.js')
            self.assertIn(expected, html)
            self.assertNotEqual(parse_qs(urlsplit(expected).query)['v'][0], templating.Ver)

    async def test_missing_or_escaping_asset_does_not_break_template_rendering(self):
        with patch.object(templating, 'static_folder', str(self.root)):
            for filename in ('missing.js', '../outside.js'):
                url = await self.asset_url(filename)
                self.assertEqual(parse_qs(urlsplit(url).query)['v'][0], templating.Ver)
            async with self.app.test_request_context('/'):
                self.assertEqual(templating._vertioned_url_for('yobot_user'), '/bot/user/')
