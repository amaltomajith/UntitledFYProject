"""
benchmarks/build_tasks/build.py
===============================
PCodeBench-Build: build-from-a-prompt tasks. The model starts in an empty folder and
never sees the acceptance tests. Writes tasks/<id>/ with prompt.txt, the hidden test
and a reference solution.

The solution exists only to prove each hidden test can be passed. It is never shown
to a model, and `--check` confirms it passes while an empty folder fails.

    python benchmarks/build_tasks/build.py --check
"""

from __future__ import annotations

import argparse
import shutil
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).parent
OUT = HERE / "tasks"

TASKS = {}


def task(name: str, lang: str, prompt: str, test: str, **solution: str) -> None:
    TASKS[name] = {"lang": lang, "prompt": prompt.strip(), "test": test.strip() + "\n",
                   "solution": {k.replace("_DOT_", "."): v.strip() + "\n" for k, v in solution.items()}}


task("py_roman", "python", """
Create roman.py with two functions. to_roman(n) converts an integer from 1 to 3999 to a Roman
numeral string. from_roman(s) converts a valid Roman numeral string back to an integer. Both
raise ValueError for anything invalid, including 0, 4000, non-canonical numerals such as IIII
or IC, and lower case.
""", """
import pytest
from roman import from_roman, to_roman

@pytest.mark.parametrize("n,s", [(1, "I"), (4, "IV"), (9, "IX"), (40, "XL"), (90, "XC"), (400, "CD"),
                                 (1994, "MCMXCIV"), (3999, "MMMCMXCIX")])
def test_both_ways(n, s):
    assert to_roman(n) == s and from_roman(s) == n

def test_round_trip():
    assert all(from_roman(to_roman(n)) == n for n in range(1, 4000))

@pytest.mark.parametrize("n", [0, -1, 4000])
def test_range(n):
    with pytest.raises(ValueError):
        to_roman(n)

@pytest.mark.parametrize("s", ["", "IIII", "VV", "IC", "iv", "ABC", "MMMM"])
def test_bad_numerals(s):
    with pytest.raises(ValueError):
        from_roman(s)
""", roman_DOT_py="""
PAIRS = [(1000, "M"), (900, "CM"), (500, "D"), (400, "CD"), (100, "C"), (90, "XC"), (50, "L"),
         (40, "XL"), (10, "X"), (9, "IX"), (5, "V"), (4, "IV"), (1, "I")]

def to_roman(n):
    if not isinstance(n, int) or not 1 <= n <= 3999:
        raise ValueError(n)
    out = ""
    for value, sym in PAIRS:
        while n >= value:
            out += sym
            n -= value
    return out

TABLE = {}

def from_roman(s):
    if not TABLE:
        TABLE.update({to_roman(n): n for n in range(1, 4000)})
    if s not in TABLE:
        raise ValueError(s)
    return TABLE[s]
""")

task("py_wordfreq", "python", """
Create wordfreq.py. top_words(text, n) returns the n most common words as a list of (word, count)
tuples, most common first, with ties in alphabetical order. Words are case-insensitive, made of
letters and apostrophes, and returned in lower case. Running `python wordfreq.py FILE --top N`
prints one "word count" line per word; --top defaults to 3. A missing file prints an error to
stderr and exits with code 2.
""", """
import subprocess, sys
from wordfreq import top_words

def run(*args):
    return subprocess.run([sys.executable, "wordfreq.py", *args], capture_output=True, text=True)

def test_counts_and_ties():
    assert top_words("b a B a c", 2) == [("a", 2), ("b", 2)]
    assert top_words("Don't stop, don't!", 1) == [("don't", 2)]

def test_more_than_exists():
    assert top_words("one", 5) == [("one", 1)]
    assert top_words("", 3) == []

def test_cli(tmp_path):
    f = tmp_path / "t.txt"
    f.write_text("x y x z x y")
    assert run(str(f), "--top", "2").stdout.split("\\n")[:2] == ["x 3", "y 2"]
    assert len(run(str(f)).stdout.strip().split("\\n")) == 3

def test_missing_file():
    r = run("nope.txt")
    assert r.returncode == 2 and r.stderr.strip() and not r.stdout.strip()
""", wordfreq_DOT_py="""
import argparse, collections, re, sys

def top_words(text, n):
    counts = collections.Counter(re.findall(r"[a-z']+", text.lower()))
    return sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))[:n]

if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("file")
    p.add_argument("--top", type=int, default=3)
    a = p.parse_args()
    try:
        text = open(a.file, encoding="utf-8").read()
    except OSError as e:
        print(e, file=sys.stderr)
        sys.exit(2)
    for word, count in top_words(text, a.top):
        print(word, count)
""")

task("py_bank", "python", """
Create bank.py with class Account(owner, balance=0). deposit(amount) and withdraw(amount) raise
ValueError for amounts that are not positive. withdraw raises InsufficientFunds, a subclass of
ValueError that you define, when the balance is too low, and leaves the balance unchanged.
transfer(other, amount) moves money atomically. history is a list of (kind, amount) tuples where
kind is "deposit", "withdraw", "transfer_out" or "transfer_in". A failed operation adds nothing.
""", """
import pytest
from bank import Account, InsufficientFunds

def test_basic():
    a = Account("a", 10)
    a.deposit(5); a.withdraw(3)
    assert a.balance == 12 and a.owner == "a"
    assert a.history == [("deposit", 5), ("withdraw", 3)]

@pytest.mark.parametrize("amount", [0, -1])
def test_bad_amounts(amount):
    a = Account("a", 10)
    with pytest.raises(ValueError):
        a.deposit(amount)
    with pytest.raises(ValueError):
        a.withdraw(amount)
    assert a.history == [] and a.balance == 10

def test_overdraw():
    a = Account("a", 1)
    with pytest.raises(InsufficientFunds):
        a.withdraw(2)
    assert a.balance == 1 and a.history == []
    assert issubclass(InsufficientFunds, ValueError)

def test_transfer():
    a, b = Account("a", 10), Account("b")
    a.transfer(b, 4)
    assert (a.balance, b.balance) == (6, 4)
    assert a.history == [("transfer_out", 4)] and b.history == [("transfer_in", 4)]

def test_failed_transfer_changes_nothing():
    a, b = Account("a", 1), Account("b")
    with pytest.raises(InsufficientFunds):
        a.transfer(b, 5)
    assert (a.balance, b.balance, a.history, b.history) == (1, 0, [], [])
""", bank_DOT_py="""
class InsufficientFunds(ValueError):
    pass

class Account:
    def __init__(self, owner, balance=0):
        self.owner, self.balance, self.history = owner, balance, []

    def _check(self, amount):
        if amount <= 0:
            raise ValueError(amount)

    def deposit(self, amount):
        self._check(amount)
        self.balance += amount
        self.history.append(("deposit", amount))

    def withdraw(self, amount):
        self._check(amount)
        if amount > self.balance:
            raise InsufficientFunds(amount)
        self.balance -= amount
        self.history.append(("withdraw", amount))

    def transfer(self, other, amount):
        self._check(amount)
        if amount > self.balance:
            raise InsufficientFunds(amount)
        self.balance -= amount
        other.balance += amount
        self.history.append(("transfer_out", amount))
        other.history.append(("transfer_in", amount))
""")

task("py_lru", "python", """
Create lru.py with class LRUCache(capacity). get(key, default=None) returns the value and marks it
recently used. put(key, value) stores it, evicting the least recently used key when over capacity;
updating an existing key also marks it recently used. len(cache) and `key in cache` work, and `in`
does not change the order. stats() returns {"hits": h, "misses": m, "evictions": e}. A capacity
below 1 raises ValueError.
""", """
import pytest
from lru import LRUCache

def test_eviction_order():
    c = LRUCache(2)
    c.put("a", 1); c.put("b", 2); c.get("a"); c.put("c", 3)
    assert "b" not in c and "a" in c and "c" in c and len(c) == 2

def test_update_refreshes():
    c = LRUCache(2)
    c.put("a", 1); c.put("b", 2); c.put("a", 9); c.put("c", 3)
    assert c.get("a") == 9 and c.get("b") is None

def test_in_does_not_refresh():
    c = LRUCache(2)
    c.put("a", 1); c.put("b", 2)
    assert "a" in c
    c.put("c", 3)
    assert "a" not in c

def test_stats():
    c = LRUCache(1)
    c.put("a", 1); c.get("a"); c.get("x", 5); c.put("b", 2)
    assert c.stats() == {"hits": 1, "misses": 1, "evictions": 1}
    assert c.get("nope", 7) == 7

def test_stored_none_is_a_hit():
    c = LRUCache(1)
    c.put("a", None)
    assert c.get("a", 1) is None and c.stats()["hits"] == 1

@pytest.mark.parametrize("n", [0, -1])
def test_capacity(n):
    with pytest.raises(ValueError):
        LRUCache(n)
""", lru_DOT_py="""
from collections import OrderedDict

class LRUCache:
    def __init__(self, capacity):
        if capacity < 1:
            raise ValueError(capacity)
        self.capacity, self.data = capacity, OrderedDict()
        self.hits = self.misses = self.evictions = 0

    def get(self, key, default=None):
        if key not in self.data:
            self.misses += 1
            return default
        self.hits += 1
        self.data.move_to_end(key)
        return self.data[key]

    def put(self, key, value):
        self.data[key] = value
        self.data.move_to_end(key)
        if len(self.data) > self.capacity:
            self.data.popitem(last=False)
            self.evictions += 1

    def __len__(self):
        return len(self.data)

    def __contains__(self, key):
        return key in self.data

    def stats(self):
        return {"hits": self.hits, "misses": self.misses, "evictions": self.evictions}
""")

task("py_inventory", "python", """
Create a package named inventory with three modules. inventory/store.py has class Store with
add(name, qty, price), remove(name, qty) and total_value(). add on an existing name increases qty
and replaces the price. remove raises KeyError for an unknown name and ValueError if qty is more
than held; an item reaching 0 is deleted. Quantities must be positive integers or ValueError is
raised. items is a dict of name to {"qty": q, "price": p}. inventory/report.py has
low_stock(store, threshold) returning names with qty below threshold, sorted, and
format_report(store) returning one line per item sorted by name, like "apple x3 @ 1.50", then a
last line "TOTAL 4.50". inventory/storage.py has save(store, path) and load(path), using JSON.
`from inventory import Store` must work.
""", """
import pytest
from inventory import Store
from inventory.report import format_report, low_stock
from inventory.storage import load, save

def stocked():
    s = Store()
    s.add("pear", 10, 2.0); s.add("apple", 3, 1.5)
    return s

def test_add_merges_and_reprices():
    s = stocked()
    s.add("apple", 1, 2.0)
    assert s.items["apple"] == {"qty": 4, "price": 2.0}
    assert s.total_value() == 28.0

def test_remove():
    s = stocked()
    s.remove("apple", 3)
    assert "apple" not in s.items
    with pytest.raises(KeyError):
        s.remove("kiwi", 1)
    with pytest.raises(ValueError):
        s.remove("pear", 11)
    assert s.items["pear"]["qty"] == 10

@pytest.mark.parametrize("qty", [0, -2])
def test_bad_quantity(qty):
    with pytest.raises(ValueError):
        Store().add("x", qty, 1.0)

def test_reports():
    s = stocked()
    assert low_stock(s, 5) == ["apple"]
    assert format_report(s).split("\\n") == ["apple x3 @ 1.50", "pear x10 @ 2.00", "TOTAL 24.50"]

def test_round_trip(tmp_path):
    path = tmp_path / "s.json"
    save(stocked(), path)
    assert load(path).items == stocked().items
""", **{
    "inventory/__init__.py": "from inventory.store import Store\n",
    "inventory/store.py": """
class Store:
    def __init__(self):
        self.items = {}

    @staticmethod
    def _qty(qty):
        if not isinstance(qty, int) or qty <= 0:
            raise ValueError(qty)

    def add(self, name, qty, price):
        self._qty(qty)
        held = self.items.get(name, {"qty": 0})["qty"]
        self.items[name] = {"qty": held + qty, "price": price}

    def remove(self, name, qty):
        self._qty(qty)
        if qty > self.items[name]["qty"]:
            raise ValueError(qty)
        self.items[name]["qty"] -= qty
        if not self.items[name]["qty"]:
            del self.items[name]

    def total_value(self):
        return sum(i["qty"] * i["price"] for i in self.items.values())
""",
    "inventory/report.py": """
def low_stock(store, threshold):
    return sorted(n for n, i in store.items.items() if i["qty"] < threshold)

def format_report(store):
    lines = [f"{n} x{i['qty']} @ {i['price']:.2f}" for n, i in sorted(store.items.items())]
    return "\\n".join(lines + [f"TOTAL {store.total_value():.2f}"])
""",
    "inventory/storage.py": """
import json
from inventory.store import Store

def save(store, path):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(store.items, f)

def load(path):
    store = Store()
    with open(path, encoding="utf-8") as f:
        store.items = json.load(f)
    return store
""",
})

task("py_intervals", "python", """
Create calendar_tools.py. merge(intervals) takes (start, end) tuples in any order and returns them
merged and sorted, joining ones that overlap or touch. free_slots(busy, day_start, day_end,
min_length=1) returns the gaps inside the day as a sorted list of tuples, ignoring busy time
outside the day and gaps shorter than min_length. first_common_slot(calendars, length, day_start,
day_end) takes a list of busy lists and returns the earliest (start, start + length) free for
everyone, or None. An interval with start >= end raises ValueError.
""", """
import pytest
from calendar_tools import first_common_slot, free_slots, merge

def test_merge():
    assert merge([(5, 7), (1, 3), (2, 4), (4, 5)]) == [(1, 7)]
    assert merge([(1, 2), (3, 4)]) == [(1, 2), (3, 4)]
    assert merge([]) == []

def test_bad_interval():
    with pytest.raises(ValueError):
        merge([(3, 3)])

def test_free_slots():
    assert free_slots([(10, 11), (13, 14)], 9, 17) == [(9, 10), (11, 13), (14, 17)]
    assert free_slots([(8, 10), (16, 18)], 9, 17) == [(10, 16)]
    assert free_slots([], 9, 17) == [(9, 17)]
    assert free_slots([(9, 17)], 9, 17) == []

def test_min_length():
    assert free_slots([(10, 12)], 9, 14, min_length=2) == [(12, 14)]

def test_common():
    a, b = [(9, 10), (12, 13)], [(10, 11), (13, 15)]
    assert first_common_slot([a, b], 1, 9, 17) == (11, 12)
    assert first_common_slot([a, b], 2, 9, 17) == (15, 17)
    assert first_common_slot([a, b], 3, 9, 17) is None
""", calendar_tools_DOT_py="""
def merge(intervals):
    out = []
    for s, e in sorted(intervals):
        if s >= e:
            raise ValueError((s, e))
        if out and s <= out[-1][1]:
            out[-1] = (out[-1][0], max(out[-1][1], e))
        else:
            out.append((s, e))
    return out

def free_slots(busy, day_start, day_end, min_length=1):
    slots, at = [], day_start
    for s, e in merge(busy):
        if s > at and min(s, day_end) - at >= min_length:
            slots.append((at, min(s, day_end)))
        at = max(at, e)
    if day_end - at >= min_length and at < day_end:
        slots.append((at, day_end))
    return slots

def first_common_slot(calendars, length, day_start, day_end):
    busy = [i for c in calendars for i in c]
    for s, e in free_slots(busy, day_start, day_end, length):
        return (s, s + length)
    return None
""")

task("js_slug", "node", """
Create slug.js using CommonJS (module.exports). It exports slugify(text) and uniqueSlug(text,
existing). slugify lower-cases, turns accented letters into plain ones (Crème Brûlée becomes
creme-brulee), replaces runs of anything else with one hyphen, and trims hyphens from both ends.
It throws TypeError if text is not a string. uniqueSlug returns the slug, or adds -2, -3 and so on
until it is not in the existing array.
""", """
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
""", slug_DOT_js="""
function slugify(text) {
  if (typeof text !== 'string') throw new TypeError('text must be a string');
  return text.normalize('NFD').replace(/[\\u0300-\\u036f]/g, '').toLowerCase()
    .replace(/[^a-z0-9]+/g, '-').replace(/^-+|-+$/g, '');
}
function uniqueSlug(text, existing) {
  const base = slugify(text);
  let slug = base;
  for (let n = 2; existing.includes(slug); n++) slug = `${base}-${n}`;
  return slug;
}
module.exports = { slugify, uniqueSlug };
""")

task("js_calc", "node", """
Create calc.js using CommonJS (module.exports), exporting evaluate(expression). It evaluates
arithmetic with + - * / %, parentheses, unary minus and decimal numbers, with normal precedence and
left associativity. Do not use eval or the Function constructor. It throws RangeError on division
by zero and SyntaxError on malformed input such as "1 +", "(2", "2 2" or an empty string.
""", """
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
  assert.doesNotMatch(fs.readFileSync(require.resolve('./calc.js'), 'utf8'), /\\beval\\s*\\(|new Function/);
});
""", calc_DOT_js="""
function evaluate(src) {
  const tokens = src.match(/\\d+(?:\\.\\d+)?|\\.\\d+|[-+*/%()]|\\S/g) || [];
  let i = 0;
  const fail = () => { throw new SyntaxError('bad expression'); };
  function expr() {
    let v = term();
    while (tokens[i] === '+' || tokens[i] === '-') v = tokens[i++] === '+' ? v + term() : v - term();
    return v;
  }
  function term() {
    let v = unary();
    while (['*', '/', '%'].includes(tokens[i])) {
      const op = tokens[i++];
      const r = unary();
      if (op !== '*' && r === 0) throw new RangeError('division by zero');
      v = op === '*' ? v * r : op === '/' ? v / r : v % r;
    }
    return v;
  }
  function unary() {
    if (tokens[i] === '-') { i++; return -unary(); }
    if (tokens[i] === '+') { i++; return unary(); }
    if (tokens[i] === '(') {
      i++;
      const v = expr();
      if (tokens[i++] !== ')') fail();
      return v;
    }
    if (i < tokens.length && /^[\\d.]/.test(tokens[i])) return parseFloat(tokens[i++]);
    return fail();
  }
  const out = expr();
  if (i !== tokens.length) fail();
  return out;
}
module.exports = { evaluate };
""")

task("js_events", "node", """
Create events.js using CommonJS (module.exports), exporting class EventBus. on(name, fn) subscribes
and returns a function that unsubscribes. once(name, fn) fires at most once. off(name, fn) removes a
handler, including one added with once. emit(name, ...args) calls handlers in subscription order
and returns how many ran. A handler that subscribes or unsubscribes during emit does not affect the
current emit. If a handler throws, the rest still run, and then emit throws an AggregateError
holding every error.
""", """
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
""", events_DOT_js="""
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
""")


def write() -> None:
    shutil.rmtree(OUT, ignore_errors=True)
    for name, spec in TASKS.items():
        folder = OUT / name
        (folder / "solution").mkdir(parents=True)
        (folder / "prompt.txt").write_text(spec["prompt"] + "\n", encoding="utf-8")
        hidden = "test_accept.py" if spec["lang"] == "python" else "accept.test.js"
        (folder / hidden).write_text(spec["test"], encoding="utf-8")
        for rel, text in spec["solution"].items():
            path = folder / "solution" / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")


def check() -> int:
    sys.path.insert(0, str(HERE.parent.parent))
    from puppeteer_code.assistant.buildbench import load_tasks, score
    bad = 0
    for spec in load_tasks(OUT):
        with tempfile.TemporaryDirectory() as tmp:
            empty = Path(tmp) / "empty"
            empty.mkdir()
            solved = Path(tmp) / "solved"
            shutil.copytree(spec.folder / "solution", solved)
            blank, good = score(spec, empty), score(spec, solved)
        ok = good["success"] and not blank["success"]
        bad += not ok
        print(f"{'ok ' if ok else 'BAD'} {spec.id}: solution {good['passed']}/{good['total']}, empty folder passes {blank['passed']}")
    return 1 if bad else 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true", help="prove each hidden test is passable and not trivial")
    args = parser.parse_args()
    write()
    print(f"Wrote {len(TASKS)} tasks to {OUT}")
    sys.exit(check() if args.check else 0)
