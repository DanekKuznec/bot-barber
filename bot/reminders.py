"""Reminders a day and two hours before a visit."""

import asyncio
import logging
from collections.abc import Awaitable, Callable

from .catalog import SHOP_ADDRESS
from .clock import now
from .db import Database
from .texts import booking_card

log = logging.getLogger(__name__)

CHECK_EVERY_SEC = 60

Sender = Callable[[int, str], Awaitable[None]]


async def send_due(db: Database, platform: str, send: Sender) -> int:
    day, soon = await db.due_reminders(now(), platform)
    sent = 0
    for kind, bookings, intro in (
        ("day", day, "Напоминаем: завтра вы записаны к нам."),
        ("soon", soon, "Ждём вас через пару часов."),
    ):
        for b in bookings:
            try:
                await send(
                    b.user_id,
                    f"{intro}\n\n{booking_card(b)}\n\n{SHOP_ADDRESS}\n"
                    "Если планы поменялись, отмените запись в разделе «Мои записи».",
                )
                sent += 1
            except Exception:
                log.warning("Не удалось отправить напоминание клиенту %s (%s)", b.user_id, platform)
            # mark even on failure so a blocked user isn't retried forever
            await db.mark_reminded(b.id, kind)
    return sent


async def reminder_loop(db: Database, senders: dict[str, Sender]) -> None:
    while True:
        for platform, send in senders.items():
            try:
                await send_due(db, platform, send)
            except Exception:
                log.exception("Ошибка при отправке напоминаний (%s)", platform)
        await asyncio.sleep(CHECK_EVERY_SEC)
