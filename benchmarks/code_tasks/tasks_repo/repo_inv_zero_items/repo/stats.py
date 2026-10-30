"""Small descriptive-statistics helpers."""
import math


def mean(values):
    """Arithmetic mean of a non-empty sequence."""
    if not values:
        raise ValueError("mean() of empty sequence")
    return sum(values) / len(values)


def median(values):
    """Middle value; the average of the two middle values for even lengths."""
    if not values:
        raise ValueError("median() of empty sequence")
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2 == 1:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2


def variance(values):
    """Population variance."""
    m = mean(values)
    return sum((v - m) ** 2 for v in values) / len(values)


def clamp(value, low, high):
    """Limit value to the closed interval [low, high]."""
    if low > high:
        raise ValueError("low must be <= high")
    return max(low, min(value, high))


def percentile(values, p):
    """Nearest-rank percentile, p in [0, 100]."""
    if not 0 <= p <= 100:
        raise ValueError("p must be in [0, 100]")
    ordered = sorted(values)
    if p == 0:
        return ordered[0]
    rank = math.ceil(p / 100 * len(ordered))
    return ordered[rank - 1]
