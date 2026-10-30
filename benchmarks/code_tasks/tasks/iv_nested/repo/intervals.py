"""Closed-interval utilities.  Intervals are (start, end) tuples with start <= end."""


def overlaps(a, b):
    """True if closed intervals a and b share at least one point."""
    return a[0] <= b[1] and b[0] <= a[1]


def merge(intervals):
    """Merge overlapping intervals; returns a sorted list."""
    out = []
    for start, end in sorted(intervals):
        if out and start <= out[-1][1]:
            out[-1] = (out[-1][0], end)
        else:
            out.append((start, end))
    return out


def total_length(intervals):
    """Length covered by the union of the intervals."""
    return sum(end - start for start, end in merge(intervals))


def gaps(intervals, lo, hi):
    """Uncovered sub-intervals of [lo, hi]."""
    out = []
    cursor = lo
    for start, end in merge(intervals):
        if start > cursor:
            out.append((cursor, min(start, hi)))
        cursor = max(cursor, end)
        if cursor >= hi:
            break
    if cursor < hi:
        out.append((cursor, hi))
    return out
