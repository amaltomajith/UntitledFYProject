import pytest
from bank import Account, InsufficientFunds

def test_basic():
    a = Account("a", 10)
    a.deposit(5); a.withdraw(3)
    assert a.balance == 12 and a.owner == "a"
    assert a.history == [("deposit", 5), ("withdraw", 3)]

@pytest.mark.parametrize("amount", [0, -1])
def test_bad_amounts(amount):
    a = Account("a", 10)
    with pytest.raises(ValueError):
        a.deposit(amount)
    with pytest.raises(ValueError):
        a.withdraw(amount)
    assert a.history == [] and a.balance == 10

def test_overdraw():
    a = Account("a", 1)
    with pytest.raises(InsufficientFunds):
        a.withdraw(2)
    assert a.balance == 1 and a.history == []
    assert issubclass(InsufficientFunds, ValueError)

def test_transfer():
    a, b = Account("a", 10), Account("b")
    a.transfer(b, 4)
    assert (a.balance, b.balance) == (6, 4)
    assert a.history == [("transfer_out", 4)] and b.history == [("transfer_in", 4)]

def test_failed_transfer_changes_nothing():
    a, b = Account("a", 1), Account("b")
    with pytest.raises(InsufficientFunds):
        a.transfer(b, 5)
    assert (a.balance, b.balance, a.history, b.history) == (1, 0, [], [])
