class Store:
    def __init__(self):
        self.items = {}

    @staticmethod
    def _qty(qty):
        if not isinstance(qty, int) or qty <= 0:
            raise ValueError(qty)

    def add(self, name, qty, price):
        self._qty(qty)
        held = self.items.get(name, {"qty": 0})["qty"]
        self.items[name] = {"qty": held + qty, "price": price}

    def remove(self, name, qty):
        self._qty(qty)
        if qty > self.items[name]["qty"]:
            raise ValueError(qty)
        self.items[name]["qty"] -= qty
        if not self.items[name]["qty"]:
            del self.items[name]

    def total_value(self):
        return sum(i["qty"] * i["price"] for i in self.items.values())
