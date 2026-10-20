"""Stock levels of a small shop."""


def available(stock, reserved):
    """Units that can still be sold: stock minus reserved, never below zero."""
    return max(stock - reserved, 0)


def needs_reorder(level, threshold):
    """True when the level has dropped to the reorder threshold or below."""
    return level <= threshold


def restock(levels, item, amount):
    """Add amount units of item to levels and return the new level."""
    if amount <= 0:
        raise ValueError("amount must be positive")
    levels[item] = levels.get(item, 0) + amount
    return levels[item]


def unit_price(total_cents, units):
    """Price per unit in cents, rounded down."""
    if units == 0:
        return 0
    return total_cents // units


def newest(history, count):
    """The last count entries of history, oldest first."""
    if count <= 0:
        return []
    return history[-count:]
