from inventory import Inventory
from report import budget, busy_time, describe_numbers, free_slots, headline, stock_summary


def test_describe_numbers():
    assert describe_numbers([4, 1, 3, 2]) == "n=4 mean=2.50 median=2.50 p90=4"


def test_busy_time_counts_overlap_once():
    assert busy_time([(9, 12), (10, 11), (13, 14)]) == 4


def test_free_slots():
    assert free_slots([(10, 11), (9, 10)], 8, 12) == [(8, 9), (11, 12)]


def test_headline():
    assert headline("the art of war") == ("The Art of War", "the-art-of-war")
    assert headline("Hello World") == ("Hello World", "hello-world")


def test_stock_summary():
    inv = Inventory()
    inv.add("pen", 10, 1.5)
    inv.add("ink", 2, 4.25)
    assert stock_summary(inv) == {"value": 23.5, "reorder": ["ink"]}


def test_budget():
    assert budget("1200 3 /") == 400.0
    assert budget("10 4 /") == 2.5
