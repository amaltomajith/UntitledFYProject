from inventory import Inventory
from report import budget, busy_time, describe_numbers, free_slots, headline


def test_describe_odd_sample():
    assert describe_numbers([5, 1, 3]) == "n=3 mean=3.00 median=3.00 p90=5"


def test_busy_time_touching_and_nested():
    assert busy_time([(1, 10), (2, 3), (4, 5), (10, 12)]) == 11


def test_free_slots_full_cover():
    assert free_slots([(8, 10), (9, 12)], 8, 12) == []


def test_headline_truncates():
    title, slug = headline("a guide to the art of very long headings", width=20)
    assert len(title) == 20 and title.endswith("...") and title.startswith("A Guide")
    assert slug == "a-guide-to-the-art-of-very-long-headings"


def test_stock_after_removal():
    inv = Inventory()
    inv.add("cup", 3, 2.0)
    inv.remove("cup", 3)
    assert inv.quantity("cup") == 0


def test_budget_compound():
    assert budget("100 20 - 3 /") == 26.67
