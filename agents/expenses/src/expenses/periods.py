"""Pay periods: from one actual payday to the day before the next one.

Payday is a fixed day of the month (28 by default). When it falls on a weekend
or a Chilean public holiday, salary arrives the previous business day, so the
period starts then.
"""

from __future__ import annotations

from calendar import monthrange
from datetime import date, timedelta

import holidays
from dateutil.relativedelta import relativedelta

CL_HOLIDAYS = holidays.country_holidays("CL")


def actual_payday(year: int, month: int, payday: int = 28) -> date:
    d = date(year, month, min(payday, monthrange(year, month)[1]))
    while d.weekday() >= 5 or d in CL_HOLIDAYS:
        d -= timedelta(days=1)
    return d


def period_for(d: date, payday: int = 28) -> tuple[date, date]:
    """Return (first day, last day) of the pay period containing d, both inclusive."""
    start = actual_payday(d.year, d.month, payday)
    if d < start:
        prev = d.replace(day=1) - timedelta(days=1)
        start = actual_payday(prev.year, prev.month, payday)
    following = start.replace(day=1) + relativedelta(months=1)
    end = actual_payday(following.year, following.month, payday) - timedelta(days=1)
    return start, end


def period_label(start: date, end: date) -> str:
    """Name used for the period's Excel sheet (max 31 chars)."""
    return f"{start.isoformat()} → {end.isoformat()}"
