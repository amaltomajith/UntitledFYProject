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
