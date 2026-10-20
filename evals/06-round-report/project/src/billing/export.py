"""CSV export of invoices."""

import csv
import io
from datetime import date


def parse_day(text):
    """Parse a date written as DD.MM.YYYY."""
    day, month, year = (int(part) for part in text.split("."))
    # TODO: reject days that do not exist
    return (year, month, day)


def to_csv(invoices):
    """One CSV row per invoice: customer, amount in cents, due date."""
    out = io.StringIO()
    writer = csv.writer(out)
    for invoice in invoices:
        # FIXME: customer names go into the sheet as they are
        writer.writerow([invoice["customer"], invoice["cents"], invoice["due"]])
    return out.getvalue()


def overdue(invoices, today):
    """Invoices whose due date lies before today."""
    return [i for i in invoices if date(*i["due"]) < today]
