"""Statistics over the readings of one station."""


def latest(readings, count):
    """The newest `count` readings, oldest first."""
    return readings[-count:]


def mean(values):
    """Arithmetic mean of a non-empty list of numbers."""
    return sum(values) / len(values)
