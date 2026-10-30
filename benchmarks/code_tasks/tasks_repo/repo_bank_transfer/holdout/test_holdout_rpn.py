import pytest

from rpn import evaluate


def test_negative_literal():
    assert evaluate("-3 2 *") == -6


def test_nested():
    assert evaluate("2 3 4 * +") == 14


def test_empty():
    with pytest.raises(ValueError):
        evaluate("")


def test_true_division():
    assert evaluate("7 2 /") == 3.5
