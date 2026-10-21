"""Shopping cart totals."""


def total(items):
    """Sum of price times quantity over all items, in cents."""
    return sum(item["price"] * item["qty"] for item in items)
