const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const XLSX = require('../src/client/public/static/vendor/sheetjs-0.20.3/xlsx.mini.min.js');
const window = {};
vm.runInNewContext(fs.readFileSync('src/client/public/static/clan/excel-export.js', 'utf8'), {
    window, document: {currentScript: {getAttribute() {return '/xlsx.js';}}},
});
const excel = window.ClanExcel;
const record = {cycle: 12, boss_num: 3, damage: 12000000, health_remain: 0,
    is_continue: false, return_seconds: 35, behalf: 20};
const data = [{qqid: '123456789012345678', nickname: '=SUM(1,2) 中文🙂 & <>', finished: 1.5,
    sl: 42, detail: [record, {cycle: 12, boss_num: 4, damage: 8000000, health_remain: 1, is_continue: true}]}];
const options = {today: 42, findName() {return '代刀成员';}};
function roundtrip(book) {
    const bytes = Buffer.from(XLSX.write(book, {type: 'buffer', bookType: 'xlsx', compression: true}));
    assert.equal(bytes.subarray(0, 2).toString(), 'PK', 'genuine OOXML ZIP archive');
    return XLSX.read(bytes, {type: 'buffer'}).Sheets[book.SheetNames[0]];
}
let sheet = roundtrip(excel.progressWorkbook(XLSX, data, options));
assert.equal(sheet.A3.v, data[0].qqid); assert.equal(sheet.A3.t, 's');
assert.equal(sheet.B3.v, data[0].nickname); assert.equal(sheet.B3.t, 's');
assert.equal(sheet.B3.f, undefined, 'nickname must not turn into a formula');
assert.equal(sheet.C3.v, 1.5); assert.equal(sheet.C3.t, 'n');
assert.ok(sheet.D3.v.includes('12,000,000'));
assert.ok(sheet.D3.v.includes('返35s'));
assert.ok(sheet.D3.v.includes('代刀成员代刀'));
assert.ok(sheet.E3.v.includes('8,000,000'));
assert.equal(sheet.J3.v, '已用SL');
assert.equal(sheet.A1.v, 'QQ号');
assert.ok(sheet['!merges'].some(range => range.s.c === 3 && range.e.c === 4));
sheet = roundtrip(excel.progressWorkbook(XLSX, data, {...options, today: -1}));
assert.equal(sheet.J3.v, '—');
sheet = roundtrip(excel.progressWorkbook(XLSX, data, {...options, today: 43}));
assert.equal(sheet.J3.v, '可用');
const personal = [{pcrdate: '2026/10/29', finished: 2.5, detail: [record]}];
sheet = roundtrip(excel.personalWorkbook(XLSX, personal, options));
assert.equal(sheet.A3.v, '2026/10/29');
assert.equal(sheet.B3.v, 2.5);
assert.ok(sheet.C3.v.includes('返35s'));
assert.equal(sheet['!ref'], 'A1:H3');
assert.equal(roundtrip(excel.progressWorkbook(XLSX, [], options))['!ref'], 'A1:J2');
console.log('Excel export: real XLSX, Unicode, literal IDs/names, numeric counts, details, SL and personal records passed.');
