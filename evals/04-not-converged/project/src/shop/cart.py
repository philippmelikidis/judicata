"""Shopping cart totals."""


def total(items):
    """Sum of price times quantity over all items, in cents."""
    # TODO: decide what happens with negative quantities
    return sum(item["price"] * item["qty"] for item in items)
