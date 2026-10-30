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
