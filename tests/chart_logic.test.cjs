// Calendar-axis tests use the same helpers as the standalone website.
// Run with: node --test tests/chart_logic.test.cjs
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const source = fs.readFileSync(path.join(__dirname, '../web/chart.js'), 'utf8');
const history = JSON.parse(fs.readFileSync(path.join(__dirname, '../data/processed/margin-maintenance-history.json'), 'utf8'));
const dates = history.markets.twse.map(row => ({ d: row.date }));
const dateHelpers = source.slice(source.indexOf('  const parseDate ='), source.indexOf('  const niceBounds ='));
function helpers(range) {
  const context = vm.createContext({ fixture: dates });
  return vm.runInContext(`const data = fixture; let range = ${JSON.stringify(range)};
    ${dateHelpers}
    ({subset, axisTicks})`, context);
}
test('3m has monthly labels, including both partial endpoint months', () => {
  const api = helpers('3m');
  const rows = api.subset();
  const expected = [...new Set(rows.map(row => row.d.slice(0, 7).replace('-', '/')))];
  assert.deepEqual(Array.from(api.axisTicks(rows), tick => tick.label), expected);
});
test('1y and 6m retain quarterly year/month ticks', () => {
  for (const range of ['1y', '6m']) {
    const api = helpers(range);
    for (const tick of api.axisTicks(api.subset())) assert.match(tick.label, /^\d{4}\/(01|04|07|10)$/);
  }
});
test('2y and 3y retain January / July ticks', () => {
  for (const range of ['2y', '3y']) {
    const api = helpers(range);
    for (const tick of api.axisTicks(api.subset())) assert.match(tick.label, /^\d{4}\/(01|07)$/);
  }
});
test('All and 5y have ordered unique yearly ticks', () => {
  for (const range of ['all', '5y']) {
    const api = helpers(range);
    const ticks = Array.from(api.axisTicks(api.subset()));
    assert.ok(ticks.length > 1);
    ticks.forEach(tick => assert.match(tick.label, /^\d{4}$/));
    for (let i = 1; i < ticks.length; i++) assert.ok(ticks[i].idx > ticks[i - 1].idx);
  }
});
test('YTD does not include the preceding year', () => {
  const api = helpers('ytd');
  assert.ok(api.subset().every(row => row.d.slice(0, 4) === dates.at(-1).d.slice(0, 4)));
});
