"""Tail return seconds reach reports, pending compensation images and the UI."""
import base64
from io import BytesIO
from pathlib import Path
import shutil
import subprocess
import sys
import unittest
from unittest.mock import patch

from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_battle_service as harness
from ybplugins.ybdata import Clan_challenge, Clan_group
from ybplugins.clan_battle.components import realize


class ReturnSecondsDisplayTests(unittest.IsolatedAsyncioTestCase):
    setUp = harness.BattleServiceTests.setUp
    tearDown = harness.BattleServiceTests.tearDown
    group = harness.BattleServiceTests.group

    def record(self, qqid=10, seconds=None, compensation=False, date=harness.TODAY,
               bid=0, gid=100, health=0, cycle=1):
        return Clan_challenge.create(gid=gid, bid=bid, qqid=qqid,
                                     challenge_pcrdate=date, challenge_pcrtime=1,
                                     boss_cycle=cycle, boss_num=1, boss_health_remain=health,
                                     challenge_damage=20, is_continue=compensation,
                                     return_seconds=seconds)

    async def test_report_exposes_recorded_zero_and_legacy_seconds(self):
        for seconds in (41, 0, None):
            self.record(seconds=seconds)
        self.db.execute_sql('PRAGMA reverse_unordered_selects = ON')
        reports = self.battle.get_report(100, None, None, harness.TODAY, nocache=True)
        self.assertEqual([r['return_seconds'] for r in reports], [41, 0, 90])

    async def test_real_status_image_shows_only_unconsumed_current_seconds(self):
        self.record(seconds=41)
        self.record(seconds=23)
        self.record(compensation=True)  # A compensation tail consumes, never adds.
        self.record(seconds=None)
        self.record(qqid=30, seconds=0)
        self.record(qqid=20, seconds=90, date=harness.TODAY - 1)
        self.record(qqid=20, seconds=89, bid=1)
        self.record(qqid=20, seconds=88, gid=200)
        with patch.object(realize, 'get_process_image', wraps=realize.get_process_image) as render:
            result = self.battle.challenger_info(100)
        chips = render.call_args.args[1]['补偿']
        self.assertEqual(chips['10'], '10 23s / 90s')
        self.assertEqual(chips['30'], '30 0s')
        self.assertNotIn('20', chips)
        encoded = result.split('base64://', 1)[1].split(']', 1)[0]
        with Image.open(BytesIO(base64.b64decode(encoded))) as image:
            self.assertEqual(image.format, 'JPEG')
            self.assertGreater(image.width, 400)
            self.assertGreater(image.height, 100)

    async def test_stage_defaults_grouping_and_original_tail_stage(self):
        self.battle.level_by_cycle = {'cn': [[1, 3], [4, 6], [7, 999]]}
        self.battle.bossinfo['cn'].append([300] * 5)
        self.record(cycle=1)
        self.record(cycle=4)
        self.record(cycle=7)
        self.record(cycle=7)
        self.record(cycle=7, seconds=41)
        Clan_group.update(boss_cycle=7).where(Clan_group.group_id == 100).execute()
        with patch.object(realize, 'get_process_image', wraps=realize.get_process_image) as render, \
             patch.object(realize, 'GroupStateBlock', wraps=realize.GroupStateBlock) as block:
            self.battle.challenger_info(100)
        self.assertEqual(render.call_args.args[1]['补偿']['10'], '10 90s ×2 / ?s ×2 / 41s')
        self.assertEqual(block.call_args_list[1].kwargs['data_text'], 'D')
        self.assertEqual([r['return_seconds'] for r in self.battle.get_report(100, None, None, harness.TODAY, nocache=True)],
                         [90, 90, None, None, 41])
        for cycle, label in [(1, 'B'), (4, 'C')]:
            Clan_group.update(boss_cycle=cycle).where(Clan_group.group_id == 100).execute()
            self.battle.group_data_list.clear()
            with patch.object(realize, 'GroupStateBlock', wraps=realize.GroupStateBlock) as block:
                self.battle.challenger_info(100)
            self.assertEqual(block.call_args_list[1].kwargs['data_text'], label)


class ReturnSecondsFrontendTests(unittest.TestCase):
    def test_pending_tail_table_and_tooltips(self):
        node = shutil.which('node')
        if not node:
            self.skipTest('Node is unavailable')
        script = Path(__file__).resolve().parents[1] / 'src/client/public/static/clan/progress.js'
        code = r'''
const fs = require('fs'), vm = require('vm'), assert = require('assert');
let options;
vm.runInNewContext(fs.readFileSync(process.argv[1], 'utf8'), {
    Vue: function (config) { options = config; }, Object, alert() {},
});
const app = Object.assign({}, options.data, options.methods);
app.members = [{qqid: 10, nickname: '测试', sl: 0}];
const record = (seconds, cont = false) => ({qqid: 10, cycle: 1, boss_num: 1,
    challenge_time: 1700000000, health_remain: 0, damage: 20,
    is_continue: cont, return_seconds: seconds});
app.refresh([record(41), record(23), record(null, true), record(null)]);
app.viewTails();
assert.deepStrictEqual(Array.from(app.tailsData, c => c.return_seconds), [23, null]);
assert.strictEqual(app.returnSeconds(0), '0s');
assert.strictEqual(app.returnSeconds(null), '?s');
assert.strictEqual(app.returnSeconds(undefined), '?s');
assert.ok(app.cdetail(record(41)).includes('返秒：41s'));
assert.ok(app.csummary(record(41)).includes('返41s'));
assert.ok(app.cdetail(record(null)).includes('返秒：?s'));
assert.ok(!app.cdetail(record(null, true)).includes('返秒：'));
'''
        result = subprocess.run([node, '-e', code, str(script)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        template = script.parents[2] / 'template/clan/progress.html'
        html = template.read_text(encoding='utf-8')
        self.assertIn('label="返秒"', html)
        self.assertIn('returnSeconds(scope.row.return_seconds)', html)
        self.assertNotIn('label="留言"', html)
