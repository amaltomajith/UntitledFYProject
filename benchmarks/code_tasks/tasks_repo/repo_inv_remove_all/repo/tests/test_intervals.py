from intervals import gaps, merge, overlaps, total_length


def test_overlaps():
    assert overlaps((1, 3), (3, 5))
    assert not overlaps((1, 2), (3, 4))


def test_merge_unsorted():
    assert merge([(5, 6), (1, 3), (2, 4)]) == [(1, 4), (5, 6)]


def test_merge_nested():
    assert merge([(1, 5), (2, 3)]) == [(1, 5)]


def test_total_length():
    assert total_length([(1, 3), (2, 5), (10, 11)]) == 5


def test_gaps():
    assert gaps([(2, 3), (5, 6)], 0, 8) == [(0, 2), (3, 5), (6, 8)]
    assert gaps([(0, 4)], 0, 4) == []
