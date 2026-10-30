import pytest
from roman import from_roman, to_roman

@pytest.mark.parametrize("n,s", [(1, "I"), (4, "IV"), (9, "IX"), (40, "XL"), (90, "XC"), (400, "CD"),
                                 (1994, "MCMXCIV"), (3999, "MMMCMXCIX")])
def test_both_ways(n, s):
    assert to_roman(n) == s and from_roman(s) == n

def test_round_trip():
    assert all(from_roman(to_roman(n)) == n for n in range(1, 4000))

@pytest.mark.parametrize("n", [0, -1, 4000])
def test_range(n):
    with pytest.raises(ValueError):
        to_roman(n)

@pytest.mark.parametrize("s", ["", "IIII", "VV", "IC", "iv", "ABC", "MMMM"])
def test_bad_numerals(s):
    with pytest.raises(ValueError):
        from_roman(s)
