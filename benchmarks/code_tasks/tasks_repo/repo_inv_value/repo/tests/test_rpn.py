import pytest

from rpn import evaluate, tokenize


def test_add():
    assert evaluate("3 4 +") == 7


def test_compound():
    assert evaluate("5 1 2 + 4 * + 3 -") == 14


def test_subtract_and_divide():
    assert evaluate("10 4 -") == 6
    assert evaluate("9 2 /") == 4.5


def test_too_few_operands():
    with pytest.raises(ValueError):
        evaluate("1 +")


def test_leftover_operands():
    with pytest.raises(ValueError):
        evaluate("1 2")


def test_tokenize():
    assert tokenize("1 2 +") == [1.0, 2.0, "+"]
