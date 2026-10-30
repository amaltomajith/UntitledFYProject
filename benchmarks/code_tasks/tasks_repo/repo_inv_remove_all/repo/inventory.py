"""A tiny inventory model."""


class OutOfStock(Exception):
    pass


class Inventory:
    def __init__(self):
        self._items = {}

    def add(self, name, qty, price):
        """Add qty units of name at a unit price (updates the price)."""
        if qty <= 0:
            raise ValueError("qty must be positive")
        current = self._items.get(name, (0, price))[0]
        self._items[name] = (current + qty, price)

    def remove(self, name, qty):
        """Remove qty units; raises OutOfStock if there are not enough."""
        have, price = self._items.get(name, (0, 0.0))
        if qty >= have:
            raise OutOfStock(name)
        if have - qty == 0:
            del self._items[name]
        else:
            self._items[name] = (have - qty, price)

    def quantity(self, name):
        return self._items.get(name, (0, 0.0))[0]

    def total_value(self):
        """Sum of qty * price over all items."""
        return sum(q * p for q, p in self._items.values())

    def low_stock(self, threshold):
        """Names with quantity strictly below threshold, sorted."""
        return sorted(n for n, (q, _) in self._items.items() if q < threshold)
