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
