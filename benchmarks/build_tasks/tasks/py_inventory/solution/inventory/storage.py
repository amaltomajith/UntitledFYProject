import json
from inventory.store import Store

def save(store, path):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(store.items, f)

def load(path):
    store = Store()
    with open(path, encoding="utf-8") as f:
        store.items = json.load(f)
    return store
