const test = require('node:test');
const assert = require('node:assert');
const { slugify, uniqueSlug } = require('./slug.js');

test('basics', () => {
  assert.equal(slugify('Hello, World!'), 'hello-world');
  assert.equal(slugify('  --a  b--  '), 'a-b');
  assert.equal(slugify('Crème Brûlée'), 'creme-brulee');
  assert.equal(slugify('!!!'), '');
});
test('type check', () => assert.throws(() => slugify(4), TypeError));
test('unique', () => {
  assert.equal(uniqueSlug('A b', []), 'a-b');
  assert.equal(uniqueSlug('A b', ['a-b']), 'a-b-2');
  assert.equal(uniqueSlug('A b', ['a-b', 'a-b-2']), 'a-b-3');
});
