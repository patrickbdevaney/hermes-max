def fibonacci(n: int) -> int:
    """Return the nth Fibonacci number iteratively."""
    a, b = 0, 1
    for _ in range(n):
        a, b = b, a + b
    return a


def gcd(a: int, b: int) -> int:
    """Greatest common divisor via Euclid's algorithm."""
    while b:
        a, b = b, a % b
    return a
