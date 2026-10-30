"""Bank accounts with overdraft protection.  Amounts are integer cents."""


class InsufficientFunds(Exception):
    pass


class Account:
    def __init__(self, owner, balance=0):
        if balance < 0:
            raise ValueError("opening balance cannot be negative")
        self.owner = owner
        self.balance = balance
        self.history = []

    def deposit(self, amount):
        if amount <= 0:
            raise ValueError("deposit must be positive")
        self.balance += amount
        self.history.append(("deposit", amount))

    def withdraw(self, amount):
        if amount <= 0:
            raise ValueError("withdrawal must be positive")
        if amount > self.balance:
            raise InsufficientFunds(f"{self.owner}: balance {self.balance} < {amount}")
        self.balance -= amount
        self.history.append(("deposit", amount))


def transfer(src, dst, amount):
    """Move amount from src to dst; nothing changes if src cannot pay."""
    src.withdraw(amount)
    dst.deposit(amount)


def apply_interest(account, rate_percent):
    """Add simple interest, rounded down to whole cents."""
    interest = account.balance * rate_percent // 100
    if interest > 0:
        account.deposit(interest)
    return interest
