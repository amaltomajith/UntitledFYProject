const test = require('node:test');
const assert = require('node:assert');
const { EventBus } = require('./events.js');

test('order, args and count', () => {
  const bus = new EventBus(); const log = [];
  bus.on('e', (a, b) => log.push(['1', a, b]));
  bus.on('e', (a) => log.push(['2', a]));
  assert.equal(bus.emit('e', 1, 2), 2);
  assert.deepEqual(log, [['1', 1, 2], ['2', 1]]);
  assert.equal(bus.emit('none'), 0);
});
test('unsubscribe', () => {
  const bus = new EventBus(); let n = 0;
  const stop = bus.on('e', () => n++);
  stop(); bus.emit('e');
  assert.equal(n, 0);
});
test('once and off', () => {
  const bus = new EventBus(); let n = 0; const f = () => n++;
  bus.once('e', f); bus.emit('e'); bus.emit('e');
  assert.equal(n, 1);
  bus.once('e', f); bus.off('e', f); bus.emit('e');
  assert.equal(n, 1);
});
test('changes during emit wait', () => {
  const bus = new EventBus(); const log = [];
  const later = () => log.push('later');
  bus.on('e', () => { log.push('first'); bus.on('e', later); bus.off('e', second); });
  const second = () => log.push('second');
  bus.on('e', second);
  bus.emit('e');
  assert.deepEqual(log, ['first', 'second']);
});
test('errors are collected', () => {
  const bus = new EventBus(); let ran = false;
  bus.on('e', () => { throw new Error('a'); });
  bus.on('e', () => { ran = true; });
  bus.on('e', () => { throw new Error('b'); });
  assert.throws(() => bus.emit('e'), (e) => e instanceof AggregateError && e.errors.length === 2);
  assert.ok(ran);
});
