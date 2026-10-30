import pytest

from stats import clamp, mean, median, percentile, variance


def test_mean_floats():
    assert mean([0.5, 1.5]) == 1.0


def test_median_single():
    assert median([7]) == 7


def test_median_unsorted_even():
    assert median([10, 2, 8, 4]) == 6


def test_variance_constant():
    assert variance([3, 3, 3]) == 0


def test_clamp_bounds():
    assert clamp(0, 0, 0) == 0
    with pytest.raises(ValueError):
        clamp(1, 2, 0)


def test_percentile_edges():
    assert percentile([5, 1, 3], 0) == 1
    assert percentile([1, 2, 3, 4], 50) == 2
