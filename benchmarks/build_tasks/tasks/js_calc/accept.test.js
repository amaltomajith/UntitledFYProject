const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const { evaluate } = require('./calc.js');

test('precedence', () => {
  assert.equal(evaluate('1 + 2 * 3'), 7);
  assert.equal(evaluate('(1 + 2) * 3'), 9);
  assert.equal(evaluate('10 - 4 - 3'), 3);
  assert.equal(evaluate('20 / 2 / 5'), 2);
  assert.equal(evaluate('7 % 4 + 1'), 4);
});
test('unary and decimals', () => {
  assert.equal(evaluate('-3 + 5'), 2);
  assert.equal(evaluate('2 * -(1 + 1)'), -4);
  assert.equal(evaluate('--2'), 2);
  assert.equal(evaluate('1.5 * 2'), 3);
});
test('errors', () => {
  assert.throws(() => evaluate('1 / 0'), RangeError);
  for (const bad of ['1 +', '(2', '2 2', '', ')', '1 $ 2']) assert.throws(() => evaluate(bad), SyntaxError);
});
test('no eval', () => {
  assert.doesNotMatch(fs.readFileSync(require.resolve('./calc.js'), 'utf8'), /\beval\s*\(|new Function/);
});
