import pytest
from calendar_tools import first_common_slot, free_slots, merge

def test_merge():
    assert merge([(5, 7), (1, 3), (2, 4), (4, 5)]) == [(1, 7)]
    assert merge([(1, 2), (3, 4)]) == [(1, 2), (3, 4)]
    assert merge([]) == []

def test_bad_interval():
    with pytest.raises(ValueError):
        merge([(3, 3)])

def test_free_slots():
    assert free_slots([(10, 11), (13, 14)], 9, 17) == [(9, 10), (11, 13), (14, 17)]
    assert free_slots([(8, 10), (16, 18)], 9, 17) == [(10, 16)]
    assert free_slots([], 9, 17) == [(9, 17)]
    assert free_slots([(9, 17)], 9, 17) == []

def test_min_length():
    assert free_slots([(10, 12)], 9, 14, min_length=2) == [(12, 14)]

def test_common():
    a, b = [(9, 10), (12, 13)], [(10, 11), (13, 15)]
    assert first_common_slot([a, b], 1, 9, 17) == (11, 12)
    assert first_common_slot([a, b], 2, 9, 17) == (15, 17)
    assert first_common_slot([a, b], 3, 9, 17) is None
