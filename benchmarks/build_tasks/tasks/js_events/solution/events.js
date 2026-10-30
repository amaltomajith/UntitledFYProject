class EventBus {
  constructor() { this.handlers = new Map(); }
  on(name, fn) {
    if (!this.handlers.has(name)) this.handlers.set(name, []);
    const entry = { fn, run: fn };
    this.handlers.get(name).push(entry);
    return () => this._remove(name, entry);
  }
  once(name, fn) {
    const stop = this.on(name, fn);
    const list = this.handlers.get(name);
    list[list.length - 1].run = (...args) => { stop(); fn(...args); };
    return stop;
  }
  _remove(name, entry) {
    const list = this.handlers.get(name) || [];
    const i = list.indexOf(entry);
    if (i >= 0) list.splice(i, 1);
  }
  off(name, fn) {
    const list = this.handlers.get(name) || [];
    const entry = list.find((e) => e.fn === fn);
    if (entry) this._remove(name, entry);
  }
  emit(name, ...args) {
    const list = [...(this.handlers.get(name) || [])];
    const errors = [];
    for (const entry of list) {
      try { entry.run(...args); } catch (e) { errors.push(e); }
    }
    if (errors.length) throw new AggregateError(errors, 'handlers failed');
    return list.length;
  }
}
module.exports = { EventBus };
