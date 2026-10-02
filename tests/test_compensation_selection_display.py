"""Selected compensation sources stay consistent in the image, API and table."""
import shutil
import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch

import test_battle_service as harness
import test_battle_routes as routes
from ybplugins.ybdata import Clan_challenge
from ybplugins.clan_battle.components import realize
from ybplugins.clan_battle.components.tail_return_seconds import pending_compensations


class SelectedCompensationDisplayTests(unittest.IsolatedAsyncioTestCase):
    setUp = routes.BattleRouteTests.setUp
    tearDown = routes.BattleRouteTests.tearDown
    login = routes.BattleRouteTests.login
    call = routes.BattleRouteTests.call
    group = harness.BattleServiceTests.group

    def record(self, seconds=None, source=None, compensation=False, qqid=10,
               date=harness.TODAY):
        return Clan_challenge.create(gid=100, bid=0, qqid=qqid,
            challenge_pcrdate=date, challenge_pcrtime=1, boss_cycle=1,
            boss_num=1, boss_health_remain=0, challenge_damage=20,
            is_continue=compensation, return_seconds=seconds,
            consumed_tail_id=source)

    async def test_selected_source_controls_image_edit_permissions_and_mutation(self):
        first = self.record(41)
        selected = self.record(80)
        third = self.record(60)
        self.record(compensation=True, source=selected.cid)
        with patch.object(realize, 'get_process_image', wraps=realize.get_process_image) as render:
            self.battle.challenger_info(100)
        self.assertEqual(render.call_args.args[1]['补偿']['10'], '10 41s / 60s')
        await self.login(10)
        with patch('ybplugins.clan_battle.components.web_operation.pcr_datetime',
                   return_value=(harness.TODAY, 1)):
            report = (await self.call(dict(action='get_challenge', ts=None)))['challenges']
        editable = {r['record_id'] for r in report if r['can_edit_return_seconds']}
        self.assertEqual(editable, {first.cid, third.cid})
        rejected = await self.call(dict(action='set_return_seconds',
            record_id=selected.cid, return_seconds=70))
        self.assertEqual(rejected['code'], 30)
        accepted = await self.call(dict(action='set_return_seconds',
            record_id=first.cid, return_seconds=70))
        self.assertEqual(accepted['code'], 0)

    async def test_legacy_fifo_and_selected_sources_replay_together(self):
        first = self.record(41)
        selected = self.record(80)
        third = self.record(60)
        other = self.record(90, qqid=30)
        yesterday = self.record(90, date=harness.TODAY - 1)
        self.record(compensation=True, source=selected.cid)
        self.record(compensation=True)
        pending = pending_compensations(self.battle, Clan_challenge.select(), 'cn')
        self.assertEqual([r.cid for r in pending], [third.cid, other.cid, yesterday.cid])
        self.assertNotIn(first.cid, [r.cid for r in pending])


class SelectedCompensationFrontendTests(unittest.TestCase):
    def test_grid_pairs_explicit_source_then_fifo_and_keeps_pending_tails(self):
        node = shutil.which('node')
        if not node:
            self.skipTest('Node is unavailable')
        script = Path(__file__).resolve().parents[1] / 'src/client/public/static/clan/progress.js'
        code = r'''
const fs = require('fs'), vm = require('vm'), assert = require('assert');
let options;
vm.runInNewContext(fs.readFileSync(process.argv[1], 'utf8'), {
    Vue: function(config) { options = config; }, Object, alert() {},
});
const app = Object.assign({}, options.data, options.methods);
app.members = [{qqid: 10, nickname: '测试', sl: 0}];
const rec = (id, seconds, cont = false, source = null) => ({record_id: id,
    qqid: 10, cycle: 1, boss_num: 1, challenge_time: 1700000000,
    health_remain: 0, damage: 20, is_continue: cont,
    return_seconds: seconds, consumed_tail_id: source});
const records = [rec(1, 41), rec(2, 80), rec(3, 60), rec(4, null, true, 2)];
app.refresh(records);
let detail = app.progressData[0].detail;
assert.strictEqual(detail[3].record_id, 4);
assert.strictEqual(detail[1], undefined);
app.viewTails();
assert.deepStrictEqual(Array.from(app.tailsData, c => c.record_id), [1, 3]);
records.push(rec(5, null, true));
app.refresh(records);
detail = app.progressData[0].detail;
assert.strictEqual(detail[1].record_id, 5);
assert.strictEqual(detail[3].record_id, 4);
app.viewTails();
assert.deepStrictEqual(Array.from(app.tailsData, c => c.record_id), [3]);
'''
        result = subprocess.run([node, '-e', code, str(script)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
