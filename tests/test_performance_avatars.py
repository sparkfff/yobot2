"""Group ranking avatars: cache, network failure, row identity and command delivery."""
import asyncio
from io import BytesIO
import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

import httpx
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src/client'))
from ybplugins.clan_battle.components import score, kernel


class PerformanceAvatarTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.directory = Path(self.tmp.name)
        self.data = {'ranking': [{'qqid': 10, 'total_blades': 1}, {'qqid': 20, 'total_blades': 0}]}
        output = BytesIO()
        Image.new('RGB', (80, 60), '#ee2244').save(output, format='PNG')
        self.content = output.getvalue()

    def client(self, content=None, error=None):
        client = AsyncMock()
        client.__aenter__.return_value = client
        client.get.side_effect = error
        client.get.return_value = httpx.Response(200, content=self.content if content is None else content,
                                                request=httpx.Request('GET', 'https://q1.qlogo.cn/'))
        return client

    async def test_download_cache_and_skip_nonparticipants(self):
        client = self.client()
        with patch.object(score.httpx, 'AsyncClient', return_value=client):
            avatars = await score.load_performance_avatars(self.data, self.directory)
        self.assertEqual(set(avatars), {10})
        self.assertEqual(avatars[10].size, (40, 40))
        client.get.assert_awaited_once_with('https://q1.qlogo.cn/g?b=qq&nk=10&s=100')
        self.assertTrue((self.directory / 'user_profile/10.jpg').is_file())
        with patch.object(score.httpx, 'AsyncClient') as factory:
            cached = await score.load_performance_avatars(self.data, self.directory)
        factory.assert_not_called()
        self.assertEqual(set(cached), {10})

    async def test_offline_refresh_keeps_stale_avatar(self):
        directory = self.directory / 'user_profile'
        directory.mkdir()
        path = directory / '10.jpg'
        path.write_bytes(self.content)
        os.utime(path, (1, 1))
        with patch.object(score.httpx, 'AsyncClient', return_value=self.client(error=httpx.ConnectError('offline'))):
            avatars = await score.load_performance_avatars(self.data, self.directory)
        self.assertEqual(avatars[10].getpixel((20, 20)), (238, 34, 68))

    async def test_invalid_or_unavailable_portrait_does_not_break_table(self):
        for client in (self.client(content=b'not an image'), self.client(error=httpx.ConnectError('offline'))):
            with patch.object(score.httpx, 'AsyncClient', return_value=client):
                self.assertEqual(await score.load_performance_avatars(self.data, self.directory), {})
        self.assertFalse((self.directory / 'user_profile/10.jpg').exists())

    async def test_batch_timeout_returns_available_images(self):
        client = self.client()
        async def slow(*args, **kwargs):
            await asyncio.sleep(10)
        client.get.side_effect = slow
        original_wait = asyncio.wait_for
        async def short_wait(task, timeout):
            return await original_wait(task, timeout=.01)
        with patch.object(score.httpx, 'AsyncClient', return_value=client), patch.object(score.asyncio, 'wait_for', short_wait):
            self.assertEqual(await score.load_performance_avatars(self.data, self.directory), {})

    async def test_command_sends_one_image_after_async_generation(self):
        battle = SimpleNamespace(setting={'public_address': 'https://example.test/', 'public_basepath': '/'},
                                 score_table=AsyncMock(return_value='[CQ:image,file=base64://test]'), api=AsyncMock())
        pending = []
        with patch.object(kernel.asyncio, 'ensure_future', side_effect=pending.append):
            result = kernel.execute(battle, 8, {'message_type': 'group', 'raw_message': '业绩表', 'group_id': 100, 'user_id': 10})
        self.assertIsNone(result)
        await pending[0]
        battle.api.send_group_msg.assert_awaited_once_with(group_id=100, message='[CQ:image,file=base64://test]')

    def test_filtered_rows_keep_correct_portraits_and_fallback(self):
        data = {'config': {'stages': [{}, {}, {}]}, 'ranking': [
            dict(qqid=20, nickname='未出刀成员', total_blades=0, stage_blades=[0]*3, stage_scores=[0]*3, score=0),
            dict(qqid=10, nickname='长昵称成员ABCDEFGHIJK', total_blades=1, stage_blades=[1,0,0], stage_scores=[1,0,0], score=1),
            dict(qqid=30, nickname='头像不可用', total_blades=1, stage_blades=[1,0,0], stage_scores=[1,0,0], score=1)]}
        image = score.render_performance_image('演示公会-2026年10月-公会战业绩表', data,
                                                {10: Image.new('RGB', (40,40), '#ee2244')})
        self.assertEqual(image.getpixel((148,229)), (238,34,68))
        self.assertEqual(image.getpixel((148,280)), (140,158,182))

