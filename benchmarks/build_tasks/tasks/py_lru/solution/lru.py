from collections import OrderedDict

class LRUCache:
    def __init__(self, capacity):
        if capacity < 1:
            raise ValueError(capacity)
        self.capacity, self.data = capacity, OrderedDict()
        self.hits = self.misses = self.evictions = 0

    def get(self, key, default=None):
        if key not in self.data:
            self.misses += 1
            return default
        self.hits += 1
        self.data.move_to_end(key)
        return self.data[key]

    def put(self, key, value):
        self.data[key] = value
        self.data.move_to_end(key)
        if len(self.data) > self.capacity:
            self.data.popitem(last=False)
            self.evictions += 1

    def __len__(self):
        return len(self.data)

    def __contains__(self, key):
        return key in self.data

    def stats(self):
        return {"hits": self.hits, "misses": self.misses, "evictions": self.evictions}
