import pytest

from bank import Account, apply_interest, transfer


def test_negative_withdrawal():
    with pytest.raises(ValueError):
        Account("x", 5).withdraw(-1)


def test_tiny_interest():
    acc = Account("x", 10)
    assert apply_interest(acc, 5) == 0
    assert acc.balance == 10 and acc.history == []


def test_negative_opening():
    with pytest.raises(ValueError):
        Account("x", -1)


def test_transfer_history():
    a, b = Account("a", 9), Account("b")
    transfer(a, b, 9)
    assert a.history == [("withdraw", 9)] and b.history == [("deposit", 9)]
