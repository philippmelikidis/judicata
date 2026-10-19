"""Monthly report over all accounts."""

from .ledger import balance


def report(entries, accounts):
    """Balance per account, in the order given."""
    # TODO: group accounts by type
    return {account: balance(entries, account) for account in accounts}
