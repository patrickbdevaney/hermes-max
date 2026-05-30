"""A tiny service layer over banking — gives the graph real call edges."""
from banking import BankAccount


def make_account(balance: float) -> BankAccount:
    """Construct a BankAccount (edge: make_account -> BankAccount)."""
    return BankAccount(balance)


def transfer(src: BankAccount, dst: BankAccount, amount: float) -> None:
    """Move money between accounts (edges: transfer -> withdraw, deposit)."""
    src.withdraw(amount)
    dst.deposit(amount)
