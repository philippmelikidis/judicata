"""The report lines for one station."""

from .stats import latest, mean


def recent(readings, count):
    """The newest `count` readings; none for a count below 1."""
    if count < 1:
        return []
    return latest(readings, count)


def summary(readings, count):
    """One line with the mean temperature of the newest `count` readings."""
    values = [r["celsius"] for r in recent(readings, count)]
    if not values:
        return "no readings"
    return f"mean {mean(values):.1f} C over {len(values)} readings"
