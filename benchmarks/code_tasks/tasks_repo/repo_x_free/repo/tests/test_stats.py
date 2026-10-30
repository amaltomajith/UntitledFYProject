import pytest

from stats import clamp, mean, median, percentile, variance


def test_mean():
    assert mean([1, 2, 3, 4]) == 2.5


def test_mean_empty():
    with pytest.raises(ValueError):
        mean([])


def test_median_odd():
    assert median([3, 1, 2]) == 2


def test_median_even():
    assert median([4, 1, 3, 2]) == 2.5


def test_variance():
    assert variance([2, 4, 4, 4, 5, 5, 7, 9]) == 4


def test_clamp():
    assert clamp(5, 0, 3) == 3
    assert clamp(-1, 0, 3) == 0
    assert clamp(2, 0, 3) == 2


def test_percentile():
    assert percentile([15, 20, 35, 40, 50], 30) == 20
    assert percentile([15, 20, 35, 40, 50], 100) == 50
