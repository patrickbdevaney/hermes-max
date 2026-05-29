class BankAccount:
    """A simple bank account supporting deposit and withdraw operations."""

    def __init__(self, balance: float = 0.0) -> None:
        self.balance = balance

    def deposit(self, amount: float) -> None:
        """Add money to the account balance."""
        self.balance += amount

    def withdraw(self, amount: float) -> None:
        """Remove money from the account, raising on insufficient funds."""
        if amount > self.balance:
            raise ValueError("insufficient funds")
        self.balance -= amount
