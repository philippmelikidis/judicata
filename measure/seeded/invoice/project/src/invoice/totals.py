"""Invoice totals in cents."""

VAT_PERCENT = 19


def vat(net_cents):
    """VAT on a net amount in cents, rounded half up to the cent."""
    return (net_cents * VAT_PERCENT + 50) // 100


def gross(net_cents):
    """Net amount plus VAT."""
    return net_cents + vat(net_cents)


def subtotal(lines):
    """Sum of quantity times unit price over all invoice lines."""
    return sum(line["qty"] * line["unit_cents"] for line in lines)


def discounted(total_cents, percent):
    """Total after a percentage discount, percent from 0 to 100."""
    if not 0 <= percent <= 100:
        raise ValueError("percent must be from 0 to 100")
    return total_cents - total_cents * percent // 100


def due_days(customer):
    """Days until payment is due."""
    return customer.get("due_days", 30)
