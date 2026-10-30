"""Reports that combine the utility modules (the cross-module layer)."""
from intervals import gaps, total_length
from rpn import evaluate
from stats import mean, median, percentile
from textutils import slugify, title_case, truncate


def describe_numbers(values):
    """One-line summary of a numeric sample."""
    return (f"n={len(values)} mean={mean(values):.2f} median={median(values):.2f} "
            f"p90={percentile(values, 90)}")


def busy_time(meetings):
    """Time covered by (start, end) meetings; overlapping time is counted once."""
    return total_length(meetings)


def free_slots(meetings, day_start, day_end):
    """Free (start, end) slots between day_start and day_end."""
    return gaps(meetings, day_start, day_end)


def headline(text, width=30):
    """(title, slug) for an article heading; the title is cut to `width` characters."""
    return truncate(title_case(text), width), slugify(text)


def stock_summary(inventory, threshold=5):
    """Total stock value (rounded to cents) and the items that need reordering."""
    return {"value": round(inventory.total_value(), 2), "reorder": inventory.low_stock(threshold)}


def budget(formula):
    """Evaluate a budget formula written in RPN, e.g. '1200 3 /' for a monthly share."""
    return round(evaluate(formula), 2)
