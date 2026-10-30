class InsufficientFunds(ValueError):
    pass

class Account:
    def __init__(self, owner, balance=0):
        self.owner, self.balance, self.history = owner, balance, []

    def _check(self, amount):
        if amount <= 0:
            raise ValueError(amount)

    def deposit(self, amount):
        self._check(amount)
        self.balance += amount
        self.history.append(("deposit", amount))

    def withdraw(self, amount):
        self._check(amount)
        if amount > self.balance:
            raise InsufficientFunds(amount)
        self.balance -= amount
        self.history.append(("withdraw", amount))

    def transfer(self, other, amount):
        self._check(amount)
        if amount > self.balance:
            raise InsufficientFunds(amount)
        self.balance -= amount
        other.balance += amount
        self.history.append(("transfer_out", amount))
        other.history.append(("transfer_in", amount))
