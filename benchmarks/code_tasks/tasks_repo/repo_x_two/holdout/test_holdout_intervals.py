from intervals import gaps, merge, overlaps, total_length


def test_merge_empty_and_touching():
    assert merge([]) == []
    assert merge([(1, 2), (2, 3)]) == [(1, 3)]


def test_overlaps_containment():
    assert overlaps((0, 10), (2, 3))


def test_total_length_empty():
    assert total_length([]) == 0


def test_gaps_edges():
    assert gaps([], 0, 5) == [(0, 5)]
    assert gaps([(0, 5)], 0, 5) == []
