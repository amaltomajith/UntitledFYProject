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
