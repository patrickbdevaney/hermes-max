def add(a: int, b: int) -> int:
    # References a name that does not exist: ruff F821 + mypy error + runtime
    # NameError when called. Deterministically red across all three stages.
    return a + undefined_variable
