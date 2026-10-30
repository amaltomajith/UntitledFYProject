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
