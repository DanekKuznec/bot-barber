"""Free-slot calculation. Pure functions, no I/O."""

from collections.abc import Iterable
from datetime import date, datetime, timedelta

from .catalog import (
    CLOSES_AT,
    DAYS_AHEAD,
    MIN_LEAD_MIN,
    OPENS_AT,
    SLOT_STEP_MIN,
    TZ,
    Master,
)

Interval = tuple[datetime, datetime]


def overlaps(a: Interval, b: Interval) -> bool:
    """Half-open intervals: a booking ending at 15:00 does not clash with one starting at 15:00."""
    return a[0] < b[1] and b[0] < a[1]


def works_on(master: Master, day: date) -> bool:
    return day.weekday() in master.workdays


def free_starts(
    day: date,
    duration_min: int,
    master: Master,
    busy: Iterable[Interval],
    now: datetime,
) -> list[datetime]:
    if not works_on(master, day):
        return []

    busy = list(busy)
    duration = timedelta(minutes=duration_min)
    step = timedelta(minutes=SLOT_STEP_MIN)
    earliest = now + timedelta(minutes=MIN_LEAD_MIN)

    start = datetime.combine(day, OPENS_AT, tzinfo=TZ)
    closes = datetime.combine(day, CLOSES_AT, tzinfo=TZ)

    result = []
    while start + duration <= closes:
        slot = (start, start + duration)
        if start >= earliest and not any(overlaps(slot, b) for b in busy):
            result.append(start)
        start += step
    return result


def slots_by_master(
    day: date,
    duration_min: int,
    masters: Iterable[Master],
    busy_by_master: dict[str, list[Interval]],
    now: datetime,
) -> dict[datetime, list[str]]:
    """Start time -> ids of masters free at that time (for the "any master" option)."""
    result: dict[datetime, list[str]] = {}
    for master in masters:
        for start in free_starts(day, duration_min, master, busy_by_master.get(master.id, []), now):
            result.setdefault(start, []).append(master.id)
    return dict(sorted(result.items()))


def booking_days(now: datetime) -> list[date]:
    today = now.astimezone(TZ).date()
    return [today + timedelta(days=i) for i in range(DAYS_AHEAD)]
