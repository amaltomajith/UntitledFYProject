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
