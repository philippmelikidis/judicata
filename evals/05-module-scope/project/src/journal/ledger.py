"""Journal entries and account balances."""


def balance(entries, account):
    """Balance of one account: debits minus credits, in cents."""
    debit = sum(e["amount"] for e in entries if e["debit"] == account)
    credit = sum(e["amount"] for e in entries if e["credit"] == account)
    return debit - credit
