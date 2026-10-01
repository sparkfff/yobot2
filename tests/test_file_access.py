import gzip
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src' / 'client'))
from quart import Quart
from werkzeug.exceptions import NotFound
from ybplugins.file_access import register_file_routes, safe_path
from ybplugins.web_util import WebUtil


class PublicFilesTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.dirs = [self.root / name for name in ('libs', 'static', 'output', 'resource')]
        for directory in self.dirs:
            directory.mkdir()
            (directory / 'ok.txt').write_text('public contents', encoding='utf-8')
        (self.root / 'secret.txt').write_text('private contents', encoding='utf-8')
        self.app = Quart(__name__, static_folder=None)
        register_file_routes(self.app, '/bot/', *self.dirs[:3], gzip_level=6)
        utility = WebUtil.__new__(WebUtil)
        utility.setting = {'public_basepath': '/bot/'}
        utility.resource_path = str(self.dirs[3])
        utility.register_routes(self.app)
        self.client = self.app.test_client()
        self.prefixes = ('/yobot-depencency/', '/bot/assets/', '/bot/output/', '/bot/resource/')

    async def test_valid_files_and_missing_files(self):
        for prefix in self.prefixes:
            response = await self.client.get(prefix + 'ok.txt')
            self.assertEqual(response.status_code, 200)
            self.assertEqual(await response.get_data(), b'public contents')
        for prefix in self.prefixes[:3]:
            response = await self.client.get(prefix + 'missing.txt')
            self.assertEqual(response.status_code, 404)

    async def test_http_traversal_rejected_before_resource_download(self):
        with patch('ybplugins.web_util.aiohttp.request') as remote:
            for prefix in self.prefixes:
                for filename in ('../secret.txt', '%2e%2e/secret.txt', '..%5csecret.txt', 'C:%5csecret.txt'):
                    for headers in ({}, {'Accept-Encoding': 'gzip'}):
                        response = await self.client.get(prefix + filename, headers=headers)
                        self.assertEqual(response.status_code, 404, prefix + filename)
            remote.assert_not_called()

    async def test_absolute_and_parent_names(self):
        for filename in ('../secret.txt', '/secret.txt', 'C:\\secret.txt', '\\secret.txt', 'nested/../ok.txt'):
            with self.assertRaises(NotFound):
                safe_path(self.dirs[0], filename)

    async def test_gzip_response_is_valid(self):
        response = await self.client.get(self.prefixes[0] + 'ok.txt', headers={'Accept-Encoding': 'gzip'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers['Content-Encoding'], 'gzip')
        self.assertEqual(gzip.decompress(await response.get_data()), b'public contents')

    async def test_resolved_escape_is_rejected_even_with_shared_path_prefix(self):
        # Exercise containment on hosts that lack the privilege to create symlinks.
        original_resolve = Path.resolve
        def resolve(path, *args, **kwargs):
            if path.name == 'linked.txt':
                return self.root / 'libs-private' / 'secret.txt'
            return original_resolve(path, *args, **kwargs)
        with patch.object(Path, 'resolve', resolve):
            with self.assertRaises(NotFound):
                safe_path(self.dirs[0], 'linked.txt')

    async def test_symlink_reads_and_gzip_writes_cannot_escape(self):
        try:
            for directory in self.dirs:
                (directory / 'escape').symlink_to(self.root, target_is_directory=True)
            (self.dirs[0] / 'ok.txt.gz').symlink_to(self.root / 'secret.txt')
        except OSError as error:
            self.skipTest('Host does not permit symbolic links: ' + str(error))
        for prefix in self.prefixes:
            response = await self.client.get(prefix + 'escape/secret.txt')
            self.assertEqual(response.status_code, 404)
        response = await self.client.get(self.prefixes[0] + 'ok.txt', headers={'Accept-Encoding': 'gzip'})
        self.assertEqual(response.status_code, 404)
        self.assertEqual((self.root / 'secret.txt').read_text(), 'private contents')

    async def test_resource_cache_write(self):
        class RemoteResponse:
            status = 200
            async def __aenter__(self):
                return self
            async def __aexit__(self, *args):
                return False
            async def read(self):
                return b'image contents'
        with patch('ybplugins.web_util.aiohttp.request', return_value=RemoteResponse()) as remote:
            response = await self.client.get('/bot/resource/nested/image.jpg')
            self.assertEqual(response.status_code, 200)
            self.assertEqual(await response.get_data(), b'image contents')
            self.assertEqual((self.dirs[3] / 'nested' / 'image.jpg').read_bytes(), b'image contents')
            self.assertTrue(remote.call_args.kwargs['url'].endswith('/nested/image.webp@w400'))
