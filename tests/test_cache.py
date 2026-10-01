import asyncio
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src' / 'client'))
from ybplugins.web_util import async_cached_func


class AsyncCacheTest(unittest.IsolatedAsyncioTestCase):
    async def test_eviction_refresh_and_operation_after_capacity(self):
        calls = []
        @async_cached_func(2)
        async def compute(key):
            calls.append(key)
            return len(calls)
        self.assertEqual(await compute('a'), 1)
        self.assertEqual(await compute('b'), 2)
        self.assertEqual(await compute('a'), 1)
        self.assertEqual(await compute('c'), 3)
        self.assertEqual(await compute('b'), 2)
        self.assertEqual(await compute('b', nocache=True), 4)
        self.assertEqual(await compute('c'), 3)
        self.assertEqual(await compute('a'), 5)

    async def test_concurrent_misses_preserve_capacity(self):
        calls = []
        @async_cached_func(2)
        async def compute(key):
            await asyncio.sleep(0)
            calls.append(key)
            return key
        self.assertEqual(await asyncio.gather(*(compute(i) for i in range(20))), list(range(20)))
        await compute(18)
        await compute(19)
        self.assertEqual(len(calls), 20)
        await compute(0)
        self.assertEqual(len(calls), 21)
        await compute(18)
        self.assertEqual(len(calls), 22)

    async def test_failed_refresh_keeps_old_value(self):
        fail = False
        @async_cached_func(1)
        async def compute(key):
            if fail:
                raise ValueError('upstream error')
            return key
        await compute('a')
        fail = True
        with self.assertRaises(ValueError):
            await compute('a', nocache=True)
        self.assertEqual(await compute('a'), 'a')

    async def test_zero_capacity_and_decorator_isolation(self):
        calls = []
        @async_cached_func(0)
        async def uncached(key):
            calls.append(key)
            return key
        await uncached(1)
        await uncached(1)
        self.assertEqual(calls, [1, 1])
        decorate = async_cached_func(1)
        @decorate
        async def first(key):
            return 'first'
        @decorate
        async def second(key):
            return 'second'
        self.assertEqual(await first(1), 'first')
        self.assertEqual(await second(1), 'second')
