"""
benchmarks/code_tasks/build_tasks.py
====================================
Generates PCodeBench-Mini: bug-fixing tasks made by injecting known bugs
(mutations) into small, self-written Python modules.

For every task the generator writes

  tasks/<id>/repo/<module>.py           the module with the bug(s)
  tasks/<id>/repo/tests/test_<module>.py visible tests (some fail)
  tasks/<id>/repo/conftest.py           makes the module importable
  tasks/<id>/holdout/test_holdout.py    hidden tests, used only for scoring
  tasks/<id>/task.json                  bug report, difficulty, split, and the
                                        reference fix as SEARCH/REPLACE blocks

The hidden tests use different inputs from the visible ones, so a "fix"
that special-cases the visible tests is caught.  Modules are split by name
into train (used by `pcode train`) and test (held out for evaluation).

    python benchmarks/code_tasks/build_tasks.py           # write tasks/
    python benchmarks/code_tasks/build_tasks.py --check   # also verify every task with pytest
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / "tasks"
CONFTEST = "import os\nimport sys\n\nsys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))\n"


def d(s: str) -> str:
    return textwrap.dedent(s).lstrip("\n")


# ═════════════════════════════════════════════════════════════════════════════
# modules: (source, visible tests, hidden tests)
# ═════════════════════════════════════════════════════════════════════════════

MODULES: dict[str, dict] = {}

MODULES["stats"] = dict(split="train", source=d('''
    """Small descriptive-statistics helpers."""
    import math


    def mean(values):
        """Arithmetic mean of a non-empty sequence."""
        if not values:
            raise ValueError("mean() of empty sequence")
        return sum(values) / len(values)


    def median(values):
        """Middle value; the average of the two middle values for even lengths."""
        if not values:
            raise ValueError("median() of empty sequence")
        ordered = sorted(values)
        mid = len(ordered) // 2
        if len(ordered) % 2 == 1:
            return ordered[mid]
        return (ordered[mid - 1] + ordered[mid]) / 2


    def variance(values):
        """Population variance."""
        m = mean(values)
        return sum((v - m) ** 2 for v in values) / len(values)


    def clamp(value, low, high):
        """Limit value to the closed interval [low, high]."""
        if low > high:
            raise ValueError("low must be <= high")
        return max(low, min(value, high))


    def percentile(values, p):
        """Nearest-rank percentile, p in [0, 100]."""
        if not 0 <= p <= 100:
            raise ValueError("p must be in [0, 100]")
        ordered = sorted(values)
        if p == 0:
            return ordered[0]
        rank = math.ceil(p / 100 * len(ordered))
        return ordered[rank - 1]
'''), tests=d('''
    import pytest

    from stats import clamp, mean, median, percentile, variance


    def test_mean():
        assert mean([1, 2, 3, 4]) == 2.5


    def test_mean_empty():
        with pytest.raises(ValueError):
            mean([])


    def test_median_odd():
        assert median([3, 1, 2]) == 2


    def test_median_even():
        assert median([4, 1, 3, 2]) == 2.5


    def test_variance():
        assert variance([2, 4, 4, 4, 5, 5, 7, 9]) == 4


    def test_clamp():
        assert clamp(5, 0, 3) == 3
        assert clamp(-1, 0, 3) == 0
        assert clamp(2, 0, 3) == 2


    def test_percentile():
        assert percentile([15, 20, 35, 40, 50], 30) == 20
        assert percentile([15, 20, 35, 40, 50], 100) == 50
'''), holdout=d('''
    import pytest

    from stats import clamp, mean, median, percentile, variance


    def test_mean_floats():
        assert mean([0.5, 1.5]) == 1.0


    def test_median_single():
        assert median([7]) == 7


    def test_median_unsorted_even():
        assert median([10, 2, 8, 4]) == 6


    def test_variance_constant():
        assert variance([3, 3, 3]) == 0


    def test_clamp_bounds():
        assert clamp(0, 0, 0) == 0
        with pytest.raises(ValueError):
            clamp(1, 2, 0)


    def test_percentile_edges():
        assert percentile([5, 1, 3], 0) == 1
        assert percentile([1, 2, 3, 4], 50) == 2
'''))

MODULES["textutils"] = dict(split="train", source=d('''
    """String helpers."""
    import re


    def slugify(text):
        """Lowercase, trim, and join words with single hyphens."""
        words = re.findall(r"[a-z0-9]+", text.lower())
        return "-".join(words)


    def truncate(text, limit, suffix="..."):
        """Cut text to at most `limit` characters, including the suffix."""
        if len(text) <= limit:
            return text
        return text[: max(0, limit - len(suffix))] + suffix


    def word_count(text):
        """Number of whitespace-separated words."""
        return len(text.split())


    def is_palindrome(text):
        """True if text reads the same backwards, ignoring case and non-alphanumerics."""
        cleaned = [c.lower() for c in text if c.isalnum()]
        return cleaned == cleaned[::-1]


    def title_case(text, minor=("a", "an", "the", "of", "and")):
        """Capitalise words except minor words; the first word is always capitalised."""
        words = text.split()
        out = []
        for i, w in enumerate(words):
            if i > 0 and w.lower() in minor:
                out.append(w.lower())
            else:
                out.append(w[:1].upper() + w[1:].lower())
        return " ".join(out)
'''), tests=d('''
    from textutils import is_palindrome, slugify, title_case, truncate, word_count


    def test_slugify():
        assert slugify("  Hello, World!  ") == "hello-world"


    def test_truncate():
        assert truncate("abcdefghij", 6) == "abc..."
        assert truncate("abc", 6) == "abc"
        assert truncate("hello", 5) == "hello"


    def test_word_count():
        assert word_count("one  two\\nthree") == 3


    def test_is_palindrome():
        assert is_palindrome("A man, a plan, a canal: Panama")
        assert not is_palindrome("abc")


    def test_title_case():
        assert title_case("the lord of the rings") == "The Lord of the Rings"
'''), holdout=d('''
    from textutils import is_palindrome, slugify, title_case, truncate, word_count


    def test_slugify_digits():
        assert slugify("Already-slugged text 42") == "already-slugged-text-42"


    def test_truncate_edges():
        assert truncate("abcdef", 6) == "abcdef"
        assert truncate("abcdefg", 3) == "..."


    def test_word_count_empty():
        assert word_count("") == 0


    def test_is_palindrome_quotes():
        assert is_palindrome("No 'x' in Nixon")


    def test_title_case_other():
        assert title_case("a tale of two cities") == "A Tale of Two Cities"
'''))

MODULES["inventory"] = dict(split="train", source=d('''
    """A tiny inventory model."""


    class OutOfStock(Exception):
        pass


    class Inventory:
        def __init__(self):
            self._items = {}

        def add(self, name, qty, price):
            """Add qty units of name at a unit price (updates the price)."""
            if qty <= 0:
                raise ValueError("qty must be positive")
            current = self._items.get(name, (0, price))[0]
            self._items[name] = (current + qty, price)

        def remove(self, name, qty):
            """Remove qty units; raises OutOfStock if there are not enough."""
            have, price = self._items.get(name, (0, 0.0))
            if qty > have:
                raise OutOfStock(name)
            if have - qty == 0:
                del self._items[name]
            else:
                self._items[name] = (have - qty, price)

        def quantity(self, name):
            return self._items.get(name, (0, 0.0))[0]

        def total_value(self):
            """Sum of qty * price over all items."""
            return sum(q * p for q, p in self._items.values())

        def low_stock(self, threshold):
            """Names with quantity strictly below threshold, sorted."""
            return sorted(n for n, (q, _) in self._items.items() if q < threshold)
'''), tests=d('''
    import pytest

    from inventory import Inventory, OutOfStock


    def test_add_accumulates():
        inv = Inventory()
        inv.add("apple", 3, 0.5)
        inv.add("apple", 2, 0.5)
        assert inv.quantity("apple") == 5


    def test_remove_all_units():
        inv = Inventory()
        inv.add("apple", 5, 0.5)
        inv.remove("apple", 2)
        assert inv.quantity("apple") == 3
        inv.remove("apple", 3)
        assert inv.quantity("apple") == 0
        assert inv.low_stock(1) == []


    def test_remove_too_many():
        inv = Inventory()
        inv.add("apple", 1, 0.5)
        with pytest.raises(OutOfStock):
            inv.remove("apple", 2)


    def test_total_value():
        inv = Inventory()
        inv.add("apple", 4, 0.5)
        inv.add("pear", 2, 1.25)
        assert inv.total_value() == 4.5


    def test_low_stock():
        inv = Inventory()
        for name, qty in [("a", 1), ("b", 5), ("c", 2), ("d", 3)]:
            inv.add(name, qty, 1.0)
        assert inv.low_stock(3) == ["a", "c"]


    def test_add_rejects_zero():
        with pytest.raises(ValueError):
            Inventory().add("x", 0, 1.0)
'''), holdout=d('''
    import pytest

    from inventory import Inventory, OutOfStock


    def test_remove_unknown():
        with pytest.raises(OutOfStock):
            Inventory().remove("ghost", 1)


    def test_price_update():
        inv = Inventory()
        inv.add("x", 1, 1.0)
        inv.add("x", 1, 2.0)
        assert inv.total_value() == 4.0


    def test_low_stock_exclusive():
        inv = Inventory()
        inv.add("x", 2, 1.0)
        assert inv.low_stock(2) == []
        assert inv.low_stock(3) == ["x"]


    def test_remove_everything():
        inv = Inventory()
        inv.add("x", 2, 3.0)
        inv.remove("x", 2)
        assert inv.total_value() == 0
        assert inv.low_stock(10) == []
'''))

MODULES["intervals"] = dict(split="train", source=d('''
    """Closed-interval utilities.  Intervals are (start, end) tuples with start <= end."""


    def overlaps(a, b):
        """True if closed intervals a and b share at least one point."""
        return a[0] <= b[1] and b[0] <= a[1]


    def merge(intervals):
        """Merge overlapping intervals; returns a sorted list."""
        out = []
        for start, end in sorted(intervals):
            if out and start <= out[-1][1]:
                out[-1] = (out[-1][0], max(out[-1][1], end))
            else:
                out.append((start, end))
        return out


    def total_length(intervals):
        """Length covered by the union of the intervals."""
        return sum(end - start for start, end in merge(intervals))


    def gaps(intervals, lo, hi):
        """Uncovered sub-intervals of [lo, hi]."""
        out = []
        cursor = lo
        for start, end in merge(intervals):
            if start > cursor:
                out.append((cursor, min(start, hi)))
            cursor = max(cursor, end)
            if cursor >= hi:
                break
        if cursor < hi:
            out.append((cursor, hi))
        return out
'''), tests=d('''
    from intervals import gaps, merge, overlaps, total_length


    def test_overlaps():
        assert overlaps((1, 3), (3, 5))
        assert not overlaps((1, 2), (3, 4))


    def test_merge_unsorted():
        assert merge([(5, 6), (1, 3), (2, 4)]) == [(1, 4), (5, 6)]


    def test_merge_nested():
        assert merge([(1, 5), (2, 3)]) == [(1, 5)]


    def test_total_length():
        assert total_length([(1, 3), (2, 5), (10, 11)]) == 5


    def test_gaps():
        assert gaps([(2, 3), (5, 6)], 0, 8) == [(0, 2), (3, 5), (6, 8)]
        assert gaps([(0, 4)], 0, 4) == []
'''), holdout=d('''
    from intervals import gaps, merge, overlaps, total_length


    def test_merge_empty_and_touching():
        assert merge([]) == []
        assert merge([(1, 2), (2, 3)]) == [(1, 3)]


    def test_overlaps_containment():
        assert overlaps((0, 10), (2, 3))


    def test_total_length_empty():
        assert total_length([]) == 0


    def test_gaps_edges():
        assert gaps([], 0, 5) == [(0, 5)]
        assert gaps([(0, 5)], 0, 5) == []
'''))

MODULES["bank"] = dict(split="train", source=d('''
    """Bank accounts with overdraft protection.  Amounts are integer cents."""


    class InsufficientFunds(Exception):
        pass


    class Account:
        def __init__(self, owner, balance=0):
            if balance < 0:
                raise ValueError("opening balance cannot be negative")
            self.owner = owner
            self.balance = balance
            self.history = []

        def deposit(self, amount):
            if amount <= 0:
                raise ValueError("deposit must be positive")
            self.balance += amount
            self.history.append(("deposit", amount))

        def withdraw(self, amount):
            if amount <= 0:
                raise ValueError("withdrawal must be positive")
            if amount > self.balance:
                raise InsufficientFunds(f"{self.owner}: balance {self.balance} < {amount}")
            self.balance -= amount
            self.history.append(("withdraw", amount))


    def transfer(src, dst, amount):
        """Move amount from src to dst; nothing changes if src cannot pay."""
        src.withdraw(amount)
        dst.deposit(amount)


    def apply_interest(account, rate_percent):
        """Add simple interest, rounded down to whole cents."""
        interest = account.balance * rate_percent // 100
        if interest > 0:
            account.deposit(interest)
        return interest
'''), tests=d('''
    import pytest

    from bank import Account, InsufficientFunds, apply_interest, transfer


    def test_deposit_withdraw():
        acc = Account("ann", 100)
        acc.deposit(50)
        acc.withdraw(30)
        assert acc.balance == 120
        assert acc.history == [("deposit", 50), ("withdraw", 30)]


    def test_overdraft_refused():
        acc = Account("ann", 10)
        with pytest.raises(InsufficientFunds):
            acc.withdraw(11)
        assert acc.balance == 10


    def test_withdraw_exact_balance():
        acc = Account("ann", 10)
        acc.withdraw(10)
        assert acc.balance == 0


    def test_transfer():
        a, b = Account("a", 100), Account("b", 0)
        transfer(a, b, 40)
        assert (a.balance, b.balance) == (60, 40)
        with pytest.raises(InsufficientFunds):
            transfer(a, b, 1000)
        assert (a.balance, b.balance) == (60, 40)


    def test_interest():
        acc = Account("ann", 1234)
        assert apply_interest(acc, 5) == 61
        assert acc.balance == 1295


    def test_deposit_zero_rejected():
        with pytest.raises(ValueError):
            Account("ann").deposit(0)
'''), holdout=d('''
    import pytest

    from bank import Account, apply_interest, transfer


    def test_negative_withdrawal():
        with pytest.raises(ValueError):
            Account("x", 5).withdraw(-1)


    def test_tiny_interest():
        acc = Account("x", 10)
        assert apply_interest(acc, 5) == 0
        assert acc.balance == 10 and acc.history == []


    def test_negative_opening():
        with pytest.raises(ValueError):
            Account("x", -1)


    def test_transfer_history():
        a, b = Account("a", 9), Account("b")
        transfer(a, b, 9)
        assert a.history == [("withdraw", 9)] and b.history == [("deposit", 9)]
'''))

MODULES["lru"] = dict(split="test", source=d('''
    """A least-recently-used cache."""
    from collections import OrderedDict


    class LRUCache:
        def __init__(self, capacity):
            if capacity <= 0:
                raise ValueError("capacity must be positive")
            self.capacity = capacity
            self._data = OrderedDict()
            self.hits = 0
            self.misses = 0

        def get(self, key, default=None):
            if key in self._data:
                self._data.move_to_end(key)
                self.hits += 1
                return self._data[key]
            self.misses += 1
            return default

        def put(self, key, value):
            if key in self._data:
                self._data.move_to_end(key)
            self._data[key] = value
            if len(self._data) > self.capacity:
                self._data.popitem(last=False)

        def __len__(self):
            return len(self._data)
'''), tests=d('''
    from lru import LRUCache


    def test_put_get():
        c = LRUCache(2)
        c.put("a", 1)
        assert c.get("a") == 1 and len(c) == 1


    def test_evicts_least_recently_used():
        c = LRUCache(2)
        c.put("a", 1)
        c.put("b", 2)
        c.get("a")
        c.put("c", 3)
        assert c.get("b") is None
        assert c.get("a") == 1 and c.get("c") == 3


    def test_update_existing():
        c = LRUCache(2)
        c.put("a", 1)
        c.put("a", 2)
        assert len(c) == 1 and c.get("a") == 2


    def test_stats():
        c = LRUCache(2)
        c.put("a", 1)
        c.get("a")
        c.get("zzz")
        assert (c.hits, c.misses) == (1, 1)
'''), holdout=d('''
    import pytest

    from lru import LRUCache


    def test_capacity_one():
        c = LRUCache(1)
        c.put("a", 1)
        c.put("b", 2)
        assert c.get("a") is None and c.get("b") == 2


    def test_default():
        assert LRUCache(3).get("k", "dflt") == "dflt"


    def test_put_refreshes_recency():
        c = LRUCache(2)
        c.put("a", 1)
        c.put("b", 2)
        c.put("a", 3)
        c.put("c", 4)
        assert c.get("b") is None and c.get("a") == 3


    def test_bad_capacity():
        with pytest.raises(ValueError):
            LRUCache(0)
'''))

MODULES["rpn"] = dict(split="test", source=d('''
    """Reverse-Polish-notation calculator."""

    OPS = {
        "+": lambda a, b: a + b,
        "-": lambda a, b: a - b,
        "*": lambda a, b: a * b,
        "/": lambda a, b: a / b,
    }


    def tokenize(expr):
        """Split an RPN expression into tokens; numbers become floats."""
        tokens = []
        for part in expr.split():
            if part in OPS:
                tokens.append(part)
            else:
                tokens.append(float(part))
        return tokens


    def evaluate(expr):
        """Evaluate an RPN expression such as '3 4 + 2 *'."""
        stack = []
        for tok in tokenize(expr):
            if isinstance(tok, float):
                stack.append(tok)
                continue
            if len(stack) < 2:
                raise ValueError(f"not enough operands for {tok!r}")
            b = stack.pop()
            a = stack.pop()
            stack.append(OPS[tok](a, b))
        if len(stack) != 1:
            raise ValueError("malformed expression")
        return stack[0]
'''), tests=d('''
    import pytest

    from rpn import evaluate, tokenize


    def test_add():
        assert evaluate("3 4 +") == 7


    def test_compound():
        assert evaluate("5 1 2 + 4 * + 3 -") == 14


    def test_subtract_and_divide():
        assert evaluate("10 4 -") == 6
        assert evaluate("9 2 /") == 4.5


    def test_too_few_operands():
        with pytest.raises(ValueError):
            evaluate("1 +")


    def test_leftover_operands():
        with pytest.raises(ValueError):
            evaluate("1 2")


    def test_tokenize():
        assert tokenize("1 2 +") == [1.0, 2.0, "+"]
'''), holdout=d('''
    import pytest

    from rpn import evaluate


    def test_negative_literal():
        assert evaluate("-3 2 *") == -6


    def test_nested():
        assert evaluate("2 3 4 * +") == 14


    def test_empty():
        with pytest.raises(ValueError):
            evaluate("")


    def test_true_division():
        assert evaluate("7 2 /") == 3.5
'''))


# ═════════════════════════════════════════════════════════════════════════════
# mutations: (task id, module, [(original, mutated)], difficulty, bug report)
# ═════════════════════════════════════════════════════════════════════════════

M = {
    "median_even": ("return (ordered[mid - 1] + ordered[mid]) / 2",
                    "return (ordered[mid] + ordered[mid + 1]) / 2"),
    "variance_ddof": ("return sum((v - m) ** 2 for v in values) / len(values)",
                      "return sum((v - m) ** 2 for v in values) / (len(values) - 1)"),
    "clamp_swap": ("return max(low, min(value, high))", "return min(low, max(value, high))"),
    "percentile_index": ("return ordered[rank - 1]", "return ordered[rank]"),
    "mean_guard": ('    if not values:\n        raise ValueError("mean() of empty sequence")\n'
                   '    return sum(values) / len(values)',
                   "    return sum(values) / len(values)"),
    "slug_lower": ('re.findall(r"[a-z0-9]+", text.lower())', 're.findall(r"[a-z0-9]+", text)'),
    "trunc_fit": ("if len(text) <= limit:", "if len(text) < limit:"),
    "trunc_suffix": ("text[: max(0, limit - len(suffix))] + suffix", "text[:limit] + suffix"),
    "palindrome": ("return cleaned == cleaned[::-1]", "return cleaned == cleaned.reverse()"),
    "title_first": ("if i > 0 and w.lower() in minor:", "if w.lower() in minor:"),
    "inv_add": ("self._items[name] = (current + qty, price)", "self._items[name] = (qty, price)"),
    "inv_remove_all": ("if qty > have:", "if qty >= have:"),
    "inv_value": ("return sum(q * p for q, p in self._items.values())",
                  "return sum(q + p for q, p in self._items.values())"),
    "inv_low": ("if q < threshold)", "if q <= threshold)"),
    "inv_zero": ("if have - qty == 0:", "if have - qty < 0:"),
    "iv_touch": ("return a[0] <= b[1] and b[0] <= a[1]", "return a[0] < b[1] and b[0] < a[1]"),
    "iv_nested": ("out[-1] = (out[-1][0], max(out[-1][1], end))", "out[-1] = (out[-1][0], end)"),
    "iv_sort": ("for start, end in sorted(intervals):", "for start, end in intervals:"),
    "iv_gap_end": ("    if cursor < hi:\n        out.append((cursor, hi))",
                   "    if cursor <= hi:\n        out.append((cursor, hi))"),
    "bank_exact": ("if amount > self.balance:", "if amount >= self.balance:"),
    "bank_transfer": ("    src.withdraw(amount)\n    dst.deposit(amount)",
                      "    dst.deposit(amount)\n    src.withdraw(amount)"),
    "bank_interest": ("interest = account.balance * rate_percent // 100",
                      "interest = account.balance * rate_percent / 100"),
    "bank_history": ('self.balance -= amount\n        self.history.append(("withdraw", amount))',
                     'self.balance -= amount\n        self.history.append(("deposit", amount))'),
    "bank_zero": ('if amount <= 0:\n            raise ValueError("deposit must be positive")',
                  'if amount < 0:\n            raise ValueError("deposit must be positive")'),
    "lru_touch": ("            self._data.move_to_end(key)\n            self.hits += 1",
                  "            self.hits += 1"),
    "lru_evict": ("self._data.popitem(last=False)", "self._data.popitem()"),
    "lru_capacity": ("if len(self._data) > self.capacity:", "if len(self._data) >= self.capacity:"),
    "lru_misses": ("        self.misses += 1\n        return default",
                   "        self.hits += 1\n        return default"),
    "rpn_order": ("        b = stack.pop()\n        a = stack.pop()",
                  "        a = stack.pop()\n        b = stack.pop()"),
    "rpn_leftover": ("if len(stack) != 1:", "if not stack:"),
    "rpn_div": ('"/": lambda a, b: a / b', '"/": lambda a, b: a // b'),
    "rpn_operands": ("if len(stack) < 2:", "if not stack:"),
}

TASKS = [
    ("stats_median_even", "stats", ["median_even"], "easy",
     "median() returns the wrong value for lists with an even number of elements."),
    ("stats_variance", "stats", ["variance_ddof"], "easy",
     "variance() is documented as the population variance, but the results are too large."),
    ("stats_clamp", "stats", ["clamp_swap"], "easy", "clamp() returns wrong values."),
    ("stats_percentile", "stats", ["percentile_index"], "medium",
     "percentile(values, 100) crashes with IndexError and other percentiles look off by one."),
    ("stats_mean_empty", "stats", ["mean_guard"], "medium",
     "mean([]) should raise ValueError with a clear message, but it raises ZeroDivisionError."),
    ("stats_two_bugs", "stats", ["median_even", "clamp_swap"], "hard",
     "Two helpers in stats.py are broken: median of even-length lists, and clamp()."),
    ("text_slugify", "textutils", ["slug_lower"], "easy",
     "slugify() drops capital letters, e.g. 'Hello World' becomes 'ello-orld'."),
    ("text_truncate_fit", "textutils", ["trunc_fit"], "easy",
     "truncate() shortens strings that already fit exactly within the limit."),
    ("text_truncate_len", "textutils", ["trunc_suffix"], "medium",
     "truncate() returns strings longer than the limit."),
    ("text_palindrome", "textutils", ["palindrome"], "easy",
     "is_palindrome() returns False for real palindromes."),
    ("text_title", "textutils", ["title_first"], "easy",
     "title_case() must always capitalise the first word, even if it is a minor word."),
    ("text_two_bugs", "textutils", ["slug_lower", "title_first"], "hard",
     "Both slugify() and title_case() produce wrong output for capitalised input."),
    ("inv_add", "inventory", ["inv_add"], "easy",
     "Adding more of an existing item replaces its quantity instead of increasing it."),
    ("inv_remove_all", "inventory", ["inv_remove_all"], "easy",
     "Removing all remaining units of an item raises OutOfStock."),
    ("inv_value", "inventory", ["inv_value"], "easy", "Inventory.total_value() is wrong."),
    ("inv_low_stock", "inventory", ["inv_low"], "medium",
     "low_stock(threshold) must list only items strictly below the threshold."),
    ("inv_zero_items", "inventory", ["inv_zero"], "medium",
     "Items whose stock reaches zero still show up in low_stock()."),
    ("iv_touching", "intervals", ["iv_touch"], "easy",
     "overlaps() must treat intervals that touch at an endpoint as overlapping (they are closed)."),
    ("iv_nested", "intervals", ["iv_nested"], "medium",
     "merge() shrinks an interval when a later interval is nested inside it."),
    ("iv_unsorted", "intervals", ["iv_sort"], "medium",
     "merge() gives wrong results when the input is not sorted."),
    ("iv_gap_end", "intervals", ["iv_gap_end"], "medium",
     "gaps() returns an empty zero-length gap when the intervals cover the whole range."),
    ("iv_two_bugs", "intervals", ["iv_nested", "iv_touch"], "hard",
     "Interval merging and overlap checks are both wrong in intervals.py."),
    ("bank_exact", "bank", ["bank_exact"], "easy",
     "Withdrawing exactly the full balance is refused with InsufficientFunds."),
    ("bank_transfer", "bank", ["bank_transfer"], "medium",
     "A failed transfer still credits the destination account."),
    ("bank_interest", "bank", ["bank_interest"], "easy",
     "apply_interest() produces fractional cents; it must round down to whole cents."),
    ("bank_history", "bank", ["bank_history"], "easy",
     "Withdrawals are recorded as deposits in the account history."),
    ("bank_zero_deposit", "bank", ["bank_zero"], "easy", "deposit(0) should be rejected."),
    ("lru_reads", "lru", ["lru_touch"], "medium",
     "The cache evicts entries that were just read; reads should count as use."),
    ("lru_evict_newest", "lru", ["lru_evict"], "easy",
     "LRUCache evicts the newest entry instead of the least recently used one."),
    ("lru_capacity", "lru", ["lru_capacity"], "easy", "LRUCache holds one item fewer than its capacity."),
    ("lru_misses", "lru", ["lru_misses"], "easy", "Cache misses are counted as hits."),
    ("lru_two_bugs", "lru", ["lru_evict", "lru_misses"], "hard",
     "LRUCache evicts the wrong entry and its hit/miss statistics are wrong."),
    ("rpn_order", "rpn", ["rpn_order"], "easy", "Subtraction and division give inverted results."),
    ("rpn_leftover", "rpn", ["rpn_leftover"], "easy",
     "evaluate('1 2') should raise ValueError because operands are left over."),
    ("rpn_division", "rpn", ["rpn_div"], "easy", "Division truncates; it should be true division."),
    ("rpn_operands", "rpn", ["rpn_operands"], "medium",
     "Operators with too few operands crash with IndexError instead of raising ValueError."),
    ("rpn_two_bugs", "rpn", ["rpn_order", "rpn_div"], "hard",
     "Division in the RPN calculator is wrong in two different ways."),
]


def solution_blocks(module: str, pairs: list[tuple[str, str]]) -> str:
    return "\n".join(f"FILE: {module}.py\n<<<<<<< SEARCH\n{mut}\n=======\n{orig}\n>>>>>>> REPLACE\n"
                     for orig, mut in pairs)


def build(out: Path = OUT) -> list[Path]:
    if out.exists():
        shutil.rmtree(out)
    paths = []
    for task_id, module, keys, difficulty, report in TASKS:
        spec = MODULES[module]
        src = spec["source"]
        pairs = [M[k] for k in keys]
        mutated = src
        for orig, mut in pairs:
            assert src.count(orig) == 1, (task_id, orig)
            mutated = mutated.replace(orig, mut, 1)
        for _, mut in pairs:
            assert mutated.count(mut) == 1, (task_id, mut)
        tdir = out / task_id
        repo = tdir / "repo"
        (repo / "tests").mkdir(parents=True)
        (tdir / "holdout").mkdir()
        (repo / f"{module}.py").write_bytes(mutated.encode())
        (repo / "tests" / f"test_{module}.py").write_bytes(spec["tests"].encode())
        (repo / "conftest.py").write_bytes(CONFTEST.encode())
        (tdir / "holdout" / "test_holdout.py").write_bytes(spec["holdout"].encode())
        meta = {
            "id": task_id, "module": module, "split": spec["split"], "difficulty": difficulty,
            "n_bugs": len(pairs), "task": report + " Fix the code so the tests pass.",
            "solution": solution_blocks(module, pairs),
        }
        (tdir / "task.json").write_text(json.dumps(meta, indent=1), encoding="utf-8")
        paths.append(tdir)
    return paths


def _pytest(cwd: Path, *targets: str) -> int:
    return subprocess.run([sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", *targets],
                          cwd=cwd, capture_output=True).returncode


def check(paths: list[Path]) -> bool:
    """Original passes visible+hidden tests; mutant fails a visible test; solution restores it."""
    sys.path.insert(0, str(HERE.parent.parent))
    from puppeteer_code.tools import apply_block, parse_edit_blocks
    ok = True
    for tdir in paths:
        meta = json.loads((tdir / "task.json").read_text(encoding="utf-8"))
        with tempfile.TemporaryDirectory() as tmp:
            work = Path(tmp) / "repo"
            shutil.copytree(tdir / "repo", work)
            shutil.copy(tdir / "holdout" / "test_holdout.py", work / "tests" / "test_holdout.py")
            mutant_fails = _pytest(work, f"tests/test_{meta['module']}.py") != 0
            content = (work / f"{meta['module']}.py").read_text(encoding="utf-8")
            for block in parse_edit_blocks(meta["solution"]):
                content, note = apply_block(content, block)
                assert content is not None, (meta["id"], note)
            (work / f"{meta['module']}.py").write_bytes(content.encode())
            fixed_passes = _pytest(work) == 0
            restored = content == MODULES[meta["module"]]["source"]
        status = "ok" if (mutant_fails and fixed_passes and restored) else "BAD"
        ok &= status == "ok"
        print(f"{status:>3}  {meta['id']:<20} mutant_fails={mutant_fails} fixed_passes={fixed_passes} "
              f"restored={restored}")
    return ok


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()
    built = build()
    print(f"wrote {len(built)} tasks to {OUT}")
    if args.check:
        sys.exit(0 if check(built) else 1)
