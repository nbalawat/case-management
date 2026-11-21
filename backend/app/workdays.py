"""Business-day arithmetic for regulatory clocks. Weekends only: a deployment adds the client's holiday calendar."""
from __future__ import annotations

from datetime import datetime, timedelta


def add_business_days(start: datetime, days: int) -> datetime:
    """The end of the business day `days` working days after `start`."""
    d = start
    while days > 0:
        d += timedelta(days=1)
        if d.weekday() < 5:
            days -= 1
    return d.replace(hour=23, minute=59, second=0, microsecond=0)
