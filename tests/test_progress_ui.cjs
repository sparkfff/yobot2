const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
let options;
const viewport = { innerWidth: 390 };
vm.runInNewContext(fs.readFileSync('src/client/public/static/clan/progress.js', 'utf8'), {
    Vue: function (config) { options = config; },
    window: viewport,
});
const methods = options.methods;
const row = { finished: 3, sl: 42, detail: [{ health_remain: 1 }] };
// Even an unexpected extra detail must never merge the final SL column.
row.detail[6] = { health_remain: 1 };
assert.equal(methods.getRowClass.call({ today: 42 }, { row }), 'finishedRow sl-used-row');
assert.equal(methods.getRowClass.call({ today: -1 }, { row }), 'finishedRow');
assert.equal(methods.getRowClass.call({ today: 43 }, { row: { finished: 1, sl: 42 } }), '');
const span = (isMobile, columnIndex) => methods.arraySpanMethod.call({ isMobile }, { row, columnIndex });
assert.deepEqual(Array.from(span(false, 4)), [1, 2]);
assert.deepEqual(Array.from(span(false, 5)), [0, 0]);
for (const index of [0, 1, 2, 3, 10]) assert.equal(span(false, index), undefined);
for (let index = 0; index < 6; index++) assert.equal(span(true, index), undefined);
const state = {};
for (const width of [360, 390, 768, 1080, 1440]) {
    viewport.innerWidth = width;
    methods.updateViewport.call(state);
    assert.equal(state.isMobile, width <= 1080);
}
console.log('Progress row states and desktop/mobile cell spans passed.');
