"""Retired analysis services must not receive clan API URLs or keys."""
from html.parser import HTMLParser
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src' / 'client'))

from quart import Quart
from ybplugins.file_access import register_file_routes
from ybplugins.templating import render_template


class PageElements(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.elements = []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        self.elements.append((tag, dict(attrs)))


class StatisticsTemplateTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.app = Quart(__name__)
        public = Path(__file__).resolve().parents[1] / 'src' / 'client' / 'public'
        register_file_routes(self.app, '/bot/', public / 'libs', public / 'static', self.tmp.name)

    async def page(self, allow_api):
        async with self.app.test_request_context('/bot/clan/100/statistics/'):
            return await render_template('clan/statistics.html', allow_api=allow_api,
                                         apikey='test-clan-secret')

    async def test_analysis_cards_disabled_and_api_key_stays_on_same_origin(self):
        html = await self.page(True)
        elements = PageElements(html).elements
        cards = [attrs for tag, attrs in elements if tag == 'el-button' and
                 attrs.get('class') == 'Service-item']
        self.assertEqual(len(cards), 4)
        self.assertEqual(sum('disabled' in card for card in cards), 1)
        self.assertEqual([card['@click'] for card in cards if '@click' in card],
                         ["location.href='./1/'", "location.href='./2/'", "location.href='./performance/'"])
        self.assertEqual(html.count('暂不可用'), 1)
        self.assertIn('业绩表', html)
        self.assertNotIn('均值偏差', html)
        for retired in ('tools.yobot.win', 'clan-battle-analyzer.pcrbot.com',
                        'encodeURIComponent(apiurl)', 'target="_blank"'):
            self.assertNotIn(retired, html)
        self.assertIn("new URL('api/', window.location.href)", html)
        self.assertIn('test-clan-secret', html)
        for tag, attrs in elements:
            if tag == 'a':
                self.assertFalse(attrs.get('href', '').startswith(('http:', 'https:', '//')))

    async def test_api_disabled_omits_key_and_copy_script(self):
        html = await self.page(False)
        self.assertNotIn('test-clan-secret', html)
        self.assertNotIn('const apiurl', html)
        self.assertNotIn("getElementById('apiurl')", html)
        self.assertIn('api访问已禁用', html)
        self.assertIn('download', html)

    async def test_chart_script_is_bundled_and_served_locally(self):
        async with self.app.test_request_context('/bot/clan/100/statistics/2/'):
            html = await render_template('clan/statistics/statistics2.html')
        scripts = [attrs['src'] for tag, attrs in PageElements(html).elements
                   if tag == 'script' and 'src' in attrs]
        self.assertTrue(all(src.startswith('/') and not src.startswith('//') for src in scripts))
        chart = '/yobot-depencency/echarts@4.7.0/dist/echarts.min.js'
        self.assertIn(chart, scripts)
        response = await self.app.test_client().get(chart)
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'echarts', await response.get_data())
