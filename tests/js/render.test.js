// node --test tests/js
const test = require('node:test');
const assert = require('node:assert/strict');
const R = require('../../web/render.js');

// every character is 20 px wide
const ctx = { font: '', measureText: s => ({ width: [...s].length * 20 }) };

test('long unbreakable words never overflow the line', () => {
  for (const text of ['Visit https://example.com/' + 'a'.repeat(80) + ' now',
    'Donaudampfschifffahrtsgesellschaftskapitän Donaudampfschifffahrtsgesellschaftskapitän']) {
    for (const balance of [false, true]) {
      const lines = R.wrap(ctx, 'f', text, 400, balance);
      assert.ok(lines.length > 1);
      for (const l of lines) assert.ok([...l].length * 20 <= 400, `${l} is wider than 400px`);
    }
  }
});

test('Japanese line-start rules', () => {
  const lines = R.wrap(ctx, 'f', 'これはテストです。とても長い文章を折り返します。', 200, false);
  for (const l of lines) assert.ok(!/^[、。」っ]/.test(l), l);
});

test('text direction follows the first strong character', () => {
  assert.equal(R.isRTL('Hello مرحبا بكم world'), false);
  assert.equal(R.isRTL('مرحبا Hello'), true);
  assert.equal(R.isRTL('123 שלום'), true);
});
