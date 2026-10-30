import pytest

from bank import Account, InsufficientFunds, apply_interest, transfer


def test_deposit_withdraw():
    acc = Account("ann", 100)
    acc.deposit(50)
    acc.withdraw(30)
    assert acc.balance == 120
    assert acc.history == [("deposit", 50), ("withdraw", 30)]


def test_overdraft_refused():
    acc = Account("ann", 10)
    with pytest.raises(InsufficientFunds):
        acc.withdraw(11)
    assert acc.balance == 10


def test_withdraw_exact_balance():
    acc = Account("ann", 10)
    acc.withdraw(10)
    assert acc.balance == 0


def test_transfer():
    a, b = Account("a", 100), Account("b", 0)
    transfer(a, b, 40)
    assert (a.balance, b.balance) == (60, 40)
    with pytest.raises(InsufficientFunds):
        transfer(a, b, 1000)
    assert (a.balance, b.balance) == (60, 40)


def test_interest():
    acc = Account("ann", 1234)
    assert apply_interest(acc, 5) == 61
    assert acc.balance == 1295


def test_deposit_zero_rejected():
    with pytest.raises(ValueError):
        Account("ann").deposit(0)
