"""Booking logic shared by the Telegram and VK frontends."""

import re
from datetime import date, datetime, timedelta
from html import escape

from .catalog import MASTER_BY_ID, MASTERS, SERVICE_BY_ID, format_price
from .db import Database, SlotTaken
from .slots import booking_days, slots_by_master
from .texts import day_long

ANY_MASTER = "any"


def master_ids(choice: str) -> list[str]:
    return [m.id for m in MASTERS] if choice == ANY_MASTER else [choice]


async def slots_for(
    db: Database, day: date, service_id: str, masters: list[str], now: datetime
) -> dict[datetime, list[str]]:
    service = SERVICE_BY_ID[service_id]
    return slots_by_master(day, service.minutes, [MASTER_BY_ID[m] for m in masters], await db.busy_by_master(day), now)


async def available_days(db: Database, service_id: str, masters: list[str], now: datetime) -> list[date]:
    return [day for day in booking_days(now) if await slots_for(db, day, service_id, masters, now)]


async def masters_with_time(db: Database, service_id: str, now: datetime) -> set[str]:
    return {m.id for m in MASTERS if await available_days(db, service_id, [m.id], now)}


def summary(data: dict) -> str:
    lines = []
    if service_id := data.get("service"):
        s = SERVICE_BY_ID[service_id]
        lines.append(f"Услуга: <b>{escape(s.title)}</b>, {format_price(s.price)}")
    if master := data.get("master"):
        name = "любой свободный" if master == ANY_MASTER else MASTER_BY_ID[master].name
        lines.append(f"Мастер: <b>{escape(name)}</b>")
    if day := data.get("day"):
        lines.append(f"День: <b>{day_long(date.fromisoformat(day))}</b>")
    return "\n".join(lines)


async def book(
    db: Database,
    *,
    platform: str,
    user_id: int,
    service_id: str,
    master_choice: str,
    assigned: str,
    start: datetime,
    now: datetime,
) -> int | None:
    """Create a booking. If the slot was taken meanwhile and the client chose "any master",
    try the other masters free at that time. Returns None when nobody is left."""
    end = start + timedelta(minutes=SERVICE_BY_ID[service_id].minutes)
    candidates = [assigned] + [m for m in master_ids(master_choice) if m != assigned]
    for master_id in candidates:
        try:
            return await db.create_booking(user_id, service_id, master_id, start, end, now, platform)
        except SlotTaken:
            continue
    return None


def normalize_phone(raw: str) -> str | None:
    digits = re.sub(r"\D", "", raw)
    if len(digits) == 10:
        digits = "7" + digits
    if len(digits) == 11 and digits[0] in "78":
        d = "7" + digits[1:]
        return f"+7 ({d[1:4]}) {d[4:7]}-{d[7:9]}-{d[9:11]}"
    return None
