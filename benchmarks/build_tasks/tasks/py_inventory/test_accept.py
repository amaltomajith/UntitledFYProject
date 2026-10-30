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
    assert format_report(s).split("\n") == ["apple x3 @ 1.50", "pear x10 @ 2.00", "TOTAL 24.50"]

def test_round_trip(tmp_path):
    path = tmp_path / "s.json"
    save(stocked(), path)
    assert load(path).items == stocked().items
