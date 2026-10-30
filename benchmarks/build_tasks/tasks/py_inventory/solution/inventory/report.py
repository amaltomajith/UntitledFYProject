def low_stock(store, threshold):
    return sorted(n for n, i in store.items.items() if i["qty"] < threshold)

def format_report(store):
    lines = [f"{n} x{i['qty']} @ {i['price']:.2f}" for n, i in sorted(store.items.items())]
    return "\n".join(lines + [f"TOTAL {store.total_value():.2f}"])
