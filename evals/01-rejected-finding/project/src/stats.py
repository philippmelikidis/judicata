"""Small statistics helpers for the weekly report."""


def mean(values):
    """Arithmetic mean of a list of numbers."""
    if not values:
        return 0.0
    return sum(values) / len(values)
